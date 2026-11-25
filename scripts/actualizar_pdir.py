"""PDIR-Now CHRS horario V11. Acumulado 07-07 Ecuador, 24 horas UTC.
Formato oficial: int16 little-endian /100; 3000x9000; longitudes 0..360.
https://persiann.eng.uci.edu/CHRSdata/PDIRNow/PDIRNow1hourly/PDIRNow_readme.txt
"""
import os,json,gzip,math,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.features import geometry_mask
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from pyproj import Transformer
from actualizar_imerg import dibujar_jenks,actualizar_guardado,save_json
ROOT=Path(__file__).resolve().parents[1]
LOCAL=timezone(timedelta(hours=-5));NODATA=-9999.
BASE='https://persiann.eng.uci.edu/CHRSdata/PDIRNow/PDIRNow1hourly/'

def period(day):
    end=datetime.strptime(day,'%Y-%m-%d').replace(hour=7,tzinfo=LOCAL)
    start=end-timedelta(days=1)
    return start,end,[start.astimezone(timezone.utc)+timedelta(hours=i) for i in range(24)]

def crop_indices(extent):
    xmin,ymin,xmax,ymax=extent
    if not (-180<xmin<xmax<0 and -60<ymin<ymax<60):raise RuntimeError('Limite fuera de cobertura esperada para Ecuador.')
    c0=math.floor((xmin+360)/.04);c1=math.ceil((xmax+360)/.04)
    r0=math.floor((60-ymax)/.04);r1=math.ceil((60-ymin)/.04)
    return r0,r1,c0,c1

def main():
    now=datetime.now(LOCAL);day=os.environ.get('FECHA_FIN','').strip() or now.date().isoformat()
    start,end,slots=period(day)
    if end>now:raise RuntimeError('El periodo aun no termina.')
    data=ROOT/'data';folder=data/'pdir';folder.mkdir(parents=True,exist_ok=True)
    status={'fuente':'PDIR-Now CHRS','intento':now.isoformat(),'fecha_fin_solicitada':day}
    if (folder/(day+'.json')).exists():
        actualizar_guardado(folder,day)
        status.update(estado='completo',mensaje='24 intervalos completos; simbologia actualizada.')
        save_json(data/'estado_pdir.json',status);return
    zones=json.loads(gzip.decompress((ROOT/'insumos/cantones.geojson.gz').read_bytes()))
    mask=json.loads((ROOT/'insumos/buffer.geojson').read_text())
    area=shape(mask['features'][0]['geometry']);r0,r1,c0,c1=crop_indices(area.bounds)
    grid=from_origin(c0*.04-360,60-r0*.04,.04,.04)
    total=np.zeros((r1-r0,c1-c0),dtype='float64');valid=np.ones(total.shape,dtype=bool)
    source_names=[];missing=[];session=requests.Session()
    for i,s in enumerate(slots,1):
        name=s.strftime('pdirnow1h%y%m%d%H.bin.gz');url=BASE+f'{s.year}/'+name
        response=None
        for attempt in range(3):
            try:
                response=session.get(url,timeout=(20,120))
                if response.status_code==404:break
                response.raise_for_status();break
            except requests.RequestException:
                if attempt==2:raise RuntimeError('No se pudo acceder a CHRS; se conservaran resultados anteriores.') from None
                time.sleep(3)
        if response.status_code==404:missing.append(name);continue
        try:raw=gzip.decompress(response.content)
        except (OSError,EOFError):raise RuntimeError('CHRS no devolvio un gzip valido: '+name) from None
        if len(raw)!=3000*9000*2:raise RuntimeError('Tamano inesperado para PDIR horario int16: '+name)
        a=np.frombuffer(raw,dtype='<i2').reshape(3000,9000)[r0:r1,c0:c1].astype('float64')
        ok=a>=0;valid &= ok;total+=np.where(ok,a/100.,0.)
        source_names.append(name);print(f'PDIR intervalo {i}/24 verificado',flush=True)
    if missing:
        status.update(estado='pendiente',mensaje=f'Faltan {len(missing)} de 24 horas; se conserva el ultimo periodo completo.',faltantes=missing)
        save_json(data/'estado_pdir.json',status);print(status['mensaje']);return
    inside=geometry_mask([mapping(area)],out_shape=total.shape,transform=grid,invert=True)
    if not np.any(valid & inside):raise RuntimeError('Sin pixels validos en el limite.')
    display=valid & inside
    output=np.where(display,total,NODATA).astype('float32')
    tif=folder/(day+'.tif')
    with rasterio.open(tif,'w',driver='GTiff',width=total.shape[1],height=total.shape[0],count=1,dtype='float32',crs='EPSG:4326',transform=grid,nodata=NODATA,compress='lzw') as dst:
        dst.write(output,1);dst.set_band_unit(1,'mm');dst.update_tags(inicio=start.isoformat(),fin=end.isoformat(),fuente='PDIR-Now horario CHRS',intervalos='24')
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
    result={'fuente':'PDIR-Now CHRS','fecha_fin':day,'inicio':start.isoformat(),'fin':end.isoformat(),
        'generado':now.isoformat(),'intervalos':24,'unidad':'mm','bounds':[[bottom,left],[top,right]],
        'imagen':f'data/pdir/{day}.png','raster':f'data/pdir/{day}.tif',
        'resolucion':'0.04 grados (aprox. 4.4 km)','metodo_media':'Ponderada por area de interseccion con celdas nativas; solo cobertura completa.',
        'metodo_maxima':'Mayor acumulado entre celdas nativas con interseccion de area positiva.',
        'leyenda':legend,'cantones':stats,'originales':source_names}
    save_json(folder/(day+'.json'),result)
    status.update(estado='completo',mensaje='24 intervalos procesados.',fecha_fin=day)
    save_json(data/'estado_pdir.json',status)
    print('PDIR: acumulado y estadisticas completos.')
if __name__=='__main__':main()
