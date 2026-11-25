"""CHIRPS v3 daily SAT. Fuente diaria oficial; no reconstruye 07-07 Ecuador.
Se prioriza final cuando existe y se conserva la etiqueta preliminar/final.
https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/readme.txt
"""
import json,gzip,re,math,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin,urlparse
import requests,numpy as np,rasterio
from rasterio.windows import Window,from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from pyproj import Transformer
from actualizar_imerg import save_json,dibujar_jenks
ROOT=Path(__file__).resolve().parents[1];LOCAL=timezone(timedelta(hours=-5));NODATA=-9999.
BASE='https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/'
class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href' and v)
def parse_files(text,base,estado):
    parser=Links();parser.feed(text);out={}
    pattern=r'chirps-v3\.0\.'+('prelim' if estado=='preliminar' else 'sat')+r'\.(\d{4})\.(\d{2})\.(\d{2})\.tif'
    for href in parser.links:
        name=href.rsplit('/',1)[-1];m=re.fullmatch(pattern,name)
        if not m:continue
        day='-'.join(m.groups());datetime.strptime(day,'%Y-%m-%d')
        url=urljoin(base,href)
        if urlparse(url).hostname!='data.chc.ucsb.edu':continue
        out[day]={'url':url,'estado':estado,'nombre':name}
    return out

def procesar(day,item):
    estado=item['estado'];now=datetime.now(LOCAL)
    start=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc);end=start+timedelta(days=1)
    data=ROOT/'data';folder=data/'chirps';folder.mkdir(parents=True,exist_ok=True)
    zones=json.loads(gzip.decompress((ROOT/'insumos/cantones.geojson.gz').read_bytes()))
    mask=json.loads((ROOT/'insumos/buffer.geojson').read_text());area=shape(mask['features'][0]['geometry'])
    cache=ROOT/'.cache/chirps';cache.mkdir(parents=True,exist_ok=True);original=cache/item['nombre']
    if not original.exists():
        tmp=original.with_suffix('.part')
        try:
            with requests.get(item['url'],stream=True,timeout=(20,180)) as r:
                r.raise_for_status()
                with tmp.open('wb') as f:
                    for block in r.iter_content(1024*1024):f.write(block)
            tmp.replace(original)
        finally:tmp.unlink(missing_ok=True)
    with rasterio.open(original) as src:
        if src.count!=1 or src.crs.to_epsg()!=4326:raise RuntimeError('GeoTIFF CHIRPS inesperado.')
        w=from_bounds(*area.bounds,transform=src.transform)
        x0,y0=math.floor(w.col_off),math.floor(w.row_off);x1,y1=math.ceil(w.col_off+w.width),math.ceil(w.row_off+w.height)
        if x0<0 or y0<0 or x1>src.width or y1>src.height:raise RuntimeError('Limite fuera de cobertura CHIRPS.')
        window=Window(x0,y0,x1-x0,y1-y0);grid=src.window_transform(window)
        a=src.read(1,window=window,masked=True)
        total=np.asarray(a.data,dtype='float64');valid=~np.ma.getmaskarray(a)&np.isfinite(total)&(total>=0)
    source_names=[item['url']]
    inside=geometry_mask([mapping(area)],out_shape=total.shape,transform=grid,invert=True)
    if not np.any(valid & inside):raise RuntimeError('Sin pixels validos en el limite.')
    display=valid & inside
    output=np.where(display,total,NODATA).astype('float32')
    tif=folder/(day+'.tif')
    with rasterio.open(tif,'w',driver='GTiff',width=total.shape[1],height=total.shape[0],count=1,dtype='float32',crs='EPSG:4326',transform=grid,nodata=NODATA,compress='lzw') as dst:
        dst.write(output,1);dst.set_band_unit(1,'mm');dst.update_tags(inicio=start.isoformat(),fin=end.isoformat(),fuente='CHIRPS v3 SAT',intervalos='1')
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
    legend=dibujar_jenks(total,display,folder/(day+'.png'))
    left,top=grid*(0,0);right,bottom=grid*(total.shape[1],total.shape[0])
    result={'fuente':'CHIRPS v3 SAT '+estado,'fecha_fin':day,'fecha_producto':day,'estado_producto':estado,'convencion_temporal':'Etiqueta diaria UTC de desagregacion; no equivale a 07h00-07h00 Ecuador.','inicio':start.isoformat(),'fin':end.isoformat(),
        'generado':now.isoformat(),'intervalos':1,'unidad':'mm','bounds':[[bottom,left],[top,right]],
        'imagen':f'data/chirps/{day}.png','raster':f'data/chirps/{day}.tif',
        'resolucion':'0.05 grados (aprox. 5.5 km); desagregacion diaria con IMERG Late','metodo_media':'Ponderada por area de interseccion con celdas nativas; solo cobertura completa.',
        'metodo_maxima':'Mayor acumulado entre celdas nativas con interseccion de area positiva.',
        'leyenda':legend,'cantones':stats,'originales':source_names}
    save_json(folder/(day+'.json'),result)

def main():
    now=datetime.now(LOCAL);data=ROOT/'data';folder=data/'chirps';folder.mkdir(parents=True,exist_ok=True)
    files={};final={};session=requests.Session()
    # Incluye ano previo para continuar funcionando durante enero.
    for year in [now.year-1,now.year]:
        for path,estado in [('prelim','preliminar'),('final','final')]:
            url=BASE+f'{path}/sat/{year}/'
            response=session.get(url,timeout=(20,90))
            if response.status_code==404:continue
            response.raise_for_status();found=parse_files(response.text,url,estado)
            if estado=='final':final.update(found)
            for day,item in found.items():
                if day<=now.date().isoformat() and (day not in files or estado=='final'):files[day]=item
    if not files:raise RuntimeError('No se encontraron archivos CHIRPS diarios.')
    latest=max(files)
    stored=sorted(p.stem for p in folder.glob('????-??-??.json'))
    new_days=sorted(d for d in files if stored and d>stored[-1])
    candidates=([latest] if not stored else new_days[:6])
    if not candidates:candidates=[latest]
    if final:candidates.append(max(final))
    # Reemplaza preliminares guardados por finales, hasta tres pendientes por ejecucion.
    upgrades=[]
    for p in folder.glob('????-??-??.json'):
        if p.stem in final and json.loads(p.read_text()).get('estado_producto')!='final':upgrades.append(p.stem)
    candidates=list(dict.fromkeys(candidates+sorted(upgrades)[:3]))
    for day in candidates:
        jp=folder/(day+'.json')
        if jp.exists() and json.loads(jp.read_text()).get('estado_producto')==files[day]['estado']:continue
        print('CHIRPS:',day,files[day]['estado'],flush=True);procesar(day,files[day])
    save_json(data/'estado_chirps.json',{'fuente':'CHIRPS v3 SAT','estado':'completo',
        'intento':now.isoformat(),'fecha_producto':latest,'estado_producto':files[latest]['estado'],
        'mensaje':'Ultima fecha publicada por CHC. Acumulado diario con etiqueta UTC, no 07h00-07h00 Ecuador.'})
if __name__=='__main__':main()
