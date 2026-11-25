"""IMERG Early GIS V07. Acumulacion 07-07 Ecuador; ejecucion sin QGIS.
Credencial solo desde NASA_PPS_EMAIL. Nunca se publica el correo.
"""
import os,json,re,math,time,gzip
from datetime import datetime,timedelta,timezone
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin,urlparse,unquote
import requests
import numpy as np
import rasterio
from rasterio.windows import Window,from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from pyproj import Transformer
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
BASE='https://jsimpsonhttps.pps.eosdis.nasa.gov/imerg/gis/early/'
LOCAL=timezone(timedelta(hours=-5))
NODATA=-9999.
class Links(HTMLParser):
    def __init__(self):super().__init__();self.hrefs=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.hrefs += [v for k,v in attrs if k=='href' and v]
def period(day):
    end=datetime.strptime(day,'%Y-%m-%d').replace(hour=7,tzinfo=LOCAL)
    start=end-timedelta(days=1)
    return start,end,[start.astimezone(timezone.utc)+timedelta(minutes=30*i) for i in range(48)]
def find_files(html,url):
    parser=Links();parser.feed(html);out={}
    for href in parser.hrefs:
        link=urljoin(url,href)
        if urlparse(link).hostname!=urlparse(BASE).hostname:continue
        name=unquote(urlparse(link).path.rsplit('/',1)[-1])
        m=re.fullmatch(r'3B-HHR-E\..*\.(\d{8})-S(\d{6})-E(\d{6})\.\d{4}\.V07[A-Z]?\.30min\.tif',name)
        if not m:continue
        d=datetime.strptime(m[1]+m[2],'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
        if d.minute not in (0,30) or d.second or m[3]!=(d+timedelta(minutes=30,seconds=-1)).strftime('%H%M%S'):continue
        if d in out and out[d]!=link:raise RuntimeError('Varias versiones para un intervalo; revisar antes de mezclar.')
        out[d]=link
    return out

def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8');temp.replace(path)
def main():
    now=datetime.now(LOCAL);day=os.environ.get('FECHA_FIN','').strip() or now.date().isoformat()
    start,end,slots=period(day)
    if end>now:raise RuntimeError('El periodo todavia no termina.')
    data=ROOT/'data';folder=data/'imerg';folder.mkdir(parents=True,exist_ok=True)
    status={'fuente':'NASA GPM IMERG Early','intento':now.isoformat(),'fecha_fin_solicitada':day}
    if (folder/(day+'.json')).exists():
        status.update(estado='completo',mensaje='Periodo ya procesado.');save_json(data/'estado.json',status);return
    email=os.environ.get('NASA_PPS_EMAIL','').strip().lower()
    if '@' not in email:raise RuntimeError('Falta el secreto NASA_PPS_EMAIL.')
    session=requests.Session();session.auth=(email,email)
    def get(url):
        if urlparse(url).hostname!=urlparse(BASE).hostname:raise RuntimeError('Servidor inesperado.')
        for attempt in range(3):
            try:
                r=session.get(url,timeout=(20,120))
                if r.status_code in (401,403):raise RuntimeError('NASA rechazo el acceso PPS. Revisa el secreto y acceso NRT.')
                r.raise_for_status();return r
            except requests.RequestException:
                if attempt==2:raise RuntimeError('No se pudo descargar un archivo de NASA; se conserva el ultimo periodo publicado.') from None
                time.sleep(3)
    files={}
    for y,m in sorted({(s.year,s.month) for s in slots}):
        url=BASE+f'{y:04}/{m:02}/';files.update(find_files(get(url).text,url))
    missing=[s for s in slots if s not in files]
    if missing:
        status.update(estado='pendiente',mensaje=f'Faltan {len(missing)} de 48 intervalos; se conserva el ultimo periodo completo.',faltantes_utc=[s.isoformat() for s in missing])
        save_json(data/'estado.json',status);print(status['mensaje']);return
    zones=json.loads(gzip.decompress((ROOT/'insumos/cantones.geojson.gz').read_bytes()))
    mask=json.loads((ROOT/'insumos/buffer.geojson').read_text())
    area=shape(mask['features'][0]['geometry']);extent=area.bounds
    cache=ROOT/'.cache'/day;cache.mkdir(parents=True,exist_ok=True)
    total=None;valid=None;reference=None;source_names=[]
    for i,s in enumerate(slots,1):
        url=files[s];name=urlparse(url).path.rsplit('/',1)[-1];path=cache/name
        if not path.exists():
            content=get(url).content
            if content[:4] not in (b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'):
                raise RuntimeError('NASA no devolvio un GeoTIFF.')
            path.write_bytes(content)
        with rasterio.open(path) as src:
            if src.count!=1 or src.dtypes[0]!='uint16' or src.crs.to_epsg()!=4326:raise RuntimeError('Formato GIS inesperado; revisar escala o CRS.')
            sig=(src.width,src.height,tuple(src.transform),str(src.crs))
            if reference is None:
                reference=sig;w=from_bounds(*extent,transform=src.transform)
                x0,y0=math.floor(w.col_off),math.floor(w.row_off)
                x1,y1=math.ceil(w.col_off+w.width),math.ceil(w.row_off+w.height)
                if x0<0 or y0<0 or x1>src.width or y1>src.height:raise RuntimeError('Fuera de cobertura.')
                window=Window(x0,y0,x1-x0,y1-y0);grid=src.window_transform(window)
                total=np.zeros((int(window.height),int(window.width)),dtype='float64');valid=np.ones(total.shape,dtype=bool)
            elif sig!=reference:raise RuntimeError('Grillas incompatibles.')
            a=src.read(1,window=window);ok=(a<29999)
            if src.nodata is not None:ok &= a!=src.nodata
            valid &= ok;total += np.where(ok,a,0)
        source_names.append(name);print(f'Intervalo {i}/48 verificado')
    total/=10. # GIS 30min es acumulacion en decimas de mm; no multiplicar por 0.5.
    inside=geometry_mask([mapping(area)],out_shape=total.shape,transform=grid,invert=True)
    if not np.any(valid & inside):raise RuntimeError('Sin pixels validos en el limite.')
    display=valid & inside
    output=np.where(display,total,NODATA).astype('float32')
    tif=folder/(day+'.tif')
    with rasterio.open(tif,'w',driver='GTiff',width=total.shape[1],height=total.shape[0],count=1,dtype='float32',crs='EPSG:4326',transform=grid,nodata=NODATA,compress='lzw') as dst:
        dst.write(output,1);dst.set_band_unit(1,'mm');dst.update_tags(inicio=start.isoformat(),fin=end.isoformat(),fuente='IMERG Early GIS V07',intervalos='48')
    # Media ponderada por area de interseccion de cada celda nativa con el canton.
    project=Transformer.from_crs(4326,32717,always_xy=True).transform
    cells=[]
    for row in range(total.shape[0]):
        for col in range(total.shape[1]):
            left,top=grid*(col,row);right,bottom=grid*(col+1,row+1)
            cell=transform(project,box(left,bottom,right,top));cells.append((cell,float(total[row,col]),bool(valid[row,col])))
    stats=[]
    for f in zones['features']:
        geom=transform(project,shape(f['geometry']));numerator=denominator=0.;maxval=None;count=0
        for cell,value,ok in cells:
            if not ok or not geom.intersects(cell):continue
            overlap=geom.intersection(cell).area
            if overlap<=0:continue
            numerator+=value*overlap;denominator+=overlap;count+=1
            maxval=value if maxval is None else max(maxval,value)
        coverage=min(1.,denominator/geom.area) if geom.area else 0.
        # No publicar una media incompleta como media de todo el canton.
        stats.append({**f['properties'],'media_mm':round(numerator/denominator,2) if denominator and coverage>=.999 else None,
            'maxima_mm':round(maxval,2) if maxval is not None and coverage>=.999 else None,
            'cobertura_valida_pct':round(100*coverage,2),'celdas':count})
    colors=np.array([[226,239,250,170],[125,197,220,185],[65,163,130,195],[248,204,76,210],[239,132,57,220],[180,42,47,225]],dtype='uint8')
    classes=np.digitize(total,[1,10,25,50,100]);rgba=colors[classes];rgba[~display]=0
    Image.fromarray(rgba).save(folder/(day+'.png'))
    left,top=grid*(0,0);right,bottom=grid*(total.shape[1],total.shape[0])
    result={'fuente':'NASA GPM IMERG Early','fecha_fin':day,'inicio':start.isoformat(),'fin':end.isoformat(),
        'generado':now.isoformat(),'intervalos':48,'unidad':'mm','bounds':[[bottom,left],[top,right]],
        'imagen':f'data/imerg/{day}.png','raster':f'data/imerg/{day}.tif',
        'resolucion':'0.1 grados (aprox. 11 km)','metodo_media':'Ponderada por area de interseccion con celdas nativas; solo cobertura completa.',
        'metodo_maxima':'Mayor acumulado entre celdas nativas con interseccion de area positiva.',
        'leyenda':{'titulo':'Acumulado estimado (mm), cortes de visualizacion propios; no son niveles de amenaza INAMHI.',
                   'rangos':['0–<1','1–<10','10–<25','25–<50','50–<100','≥100']},'cantones':stats,'originales':source_names}
    save_json(folder/(day+'.json'),result)
    days=sorted((p.stem for p in folder.glob('????-??-??.json')),reverse=True)
    save_json(data/'catalogo.json',{'imerg':days})
    status.update(estado='completo',mensaje='48 intervalos procesados.',fecha_fin=day);save_json(data/'estado.json',status)
    print('LISTO: acumulado y estadisticas generados.')
if __name__=='__main__':main()

