"""WRF diario del WCS usado por el visor INAMHI. Un TIFF por fecha, sin sumas.
La etiqueta TIME del servicio no demuestra los extremos del acumulado diario.
"""
import gzip,json,math,xml.etree.ElementTree as ET
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests,numpy as np,rasterio
from rasterio.io import MemoryFile
from rasterio.windows import Window,from_bounds
from rasterio.features import geometry_mask
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from pyproj import Transformer
from actualizar_imerg import save_json
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
LOCAL=timezone(timedelta(hours=-5));BASE='https://services.geoglows.org/geoserver/wrf/'
LAYER='wrf_precipitation_daily';NODATA=-9999.
def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
def tag(node):return node.tag.split('}')[-1]
def capabilities(session):
    r=session.get(BASE+'wms',params={'service':'WMS','version':'1.3.0','request':'GetCapabilities'},timeout=(20,120));r.raise_for_status()
    for layer in ET.fromstring(r.content).iter():
        if tag(layer)!='Layer':continue
        if not any(tag(c)=='Name' and c.text in (LAYER,'wrf:'+LAYER) for c in layer):continue
        dims={c.attrib['name'].lower():c for c in layer if tag(c)=='Dimension'}
        init=utc(dims['initd'].attrib['default'])
        times=sorted({utc(s.strip()) for s in dims['time'].text.split(',')})
        return init,[d for d in times if init<=d<init+timedelta(days=3)]
    raise RuntimeError('No se encontro WRF diario en el WMS.')
def process(session,init,date,zones,area):
    fmt=lambda d:d.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    key=init.strftime('%Y%m%dT%H%MZ')+'_'+date.strftime('%Y%m%dT%H%MZ')
    folder=ROOT/'data/wrf';folder.mkdir(parents=True,exist_ok=True)
    params={'service':'WCS','version':'1.0.0','request':'GetCoverage','coverage':'wrf:'+LAYER,
      'crs':'EPSG:4326','bbox':'-81.09890747070312,-5.032300472259522,-75.18589782714844,1.474700093269348',
      'width':219,'height':241,'format':'GeoTIFF','time':fmt(date),'dim_initd':fmt(init)}
    r=session.get(BASE+'wcs',params=params,timeout=(20,150));r.raise_for_status()
    if r.content[:4] not in (b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'):
        raise RuntimeError('El WCS no devolvio un GeoTIFF: '+r.text[:160])
    with MemoryFile(r.content) as mem,mem.open() as src:
        if src.count!=1 or src.crs.to_epsg()!=4326:raise RuntimeError('Grilla WRF inesperada.')
        if not np.isclose(abs(src.transform.a),.027,atol=1e-6):raise RuntimeError('Cambio la grilla WRF; revisar resolucion.')
        w=from_bounds(*area.bounds,transform=src.transform)
        x0,y0=math.floor(w.col_off),math.floor(w.row_off);x1,y1=math.ceil(w.col_off+w.width),math.ceil(w.row_off+w.height)
        if x0<0 or y0<0 or x1>src.width or y1>src.height:raise RuntimeError('Limite fuera de cobertura WRF.')
        window=Window(x0,y0,x1-x0,y1-y0);grid=src.window_transform(window);a=src.read(1,window=window,masked=True)
        values=a.data.astype('float64');valid=~np.ma.getmaskarray(a)&np.isfinite(values)&(values>=0)
    inside=geometry_mask([mapping(area)],out_shape=values.shape,transform=grid,invert=True)
    display=valid&inside
    if not display.any():raise RuntimeError('WRF sin datos validos dentro del limite.')
    # Es la precipitacion del TIFF recibido. No se acumulan fechas ni pixeles.
    tif=folder/(key+'.tif');temp=tif.with_suffix('.tmp.tif')
    with rasterio.open(temp,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],count=1,dtype='float32',crs='EPSG:4326',transform=grid,nodata=NODATA,compress='lzw') as dst:
        dst.write(np.where(display,values,NODATA).astype('float32'),1)
        dst.update_tags(fuente=BASE,capa=LAYER,inicializacion_solicitada=fmt(init),fecha_servicio=fmt(date),intervalo_horario='No confirmado por los metadatos del servicio')
    temp.replace(tif)
    # Comparacion territorial interna mediante media ponderada por area.
    project=Transformer.from_crs(4326,32717,always_xy=True).transform;cells=[]
    for row,col in np.ndindex(values.shape):
        if not valid[row,col]:continue
        left,top=grid*(col,row);right,bottom=grid*(col+1,row+1)
        cells.append((transform(project,box(left,bottom,right,top)),float(values[row,col])))
    ranked=[];stats=[]
    for f in zones['features']:
        geom=transform(project,shape(f['geometry']));numerator=denominator=0.
        for cell,value in cells:
            if not geom.intersects(cell):continue
            overlap=geom.intersection(cell).area
            numerator+=value*overlap;denominator+=overlap
        coverage=min(1.,denominator/geom.area) if geom.area else 0.
        item={**f['properties'],'cobertura_completa':coverage>=.999}
        stats.append(item)
        if denominator and coverage>=.999:ranked.append((numerator/denominator,item))
    ranked.sort(key=lambda p:(-p[0],str(p[1]['codigo'])))
    # Empates comparten posicion. La zona en estudio no se presenta como canton.
    previous=None;rank=0
    for i,(mean,item) in enumerate(ranked,1):
        if previous is None or not np.isclose(mean,previous,rtol=0,atol=1e-9):rank=i
        item['posicion_relativa']=rank;previous=mean
    nonzero=[item for mean,item in ranked if mean>0 and str(item.get('codigo','')).isdigit() and len(str(item['codigo']))==4]
    top=[]
    if nonzero:
        # Mostrar todos los empates en el corte de tres cantones.
        threshold=nonzero[min(2,len(nonzero)-1)]['posicion_relativa']
        top=[p for p in nonzero if p['posicion_relativa']<=threshold]
    legend=dibujar_stretched(values,display,folder/(key+'.png'))
    left,top_y=grid*(0,0);right,bottom=grid*(values.shape[1],values.shape[0])
    result={'tipo':'pronostico','fuente':'WRF · servicio del visor INAMHI','fecha_fin':date.date().isoformat(),
      'fecha_servicio':fmt(date),'inicializacion':fmt(init),'generado':datetime.now(LOCAL).isoformat(),
      'intervalos':1,'unidad':'mm','convencion_temporal':'Producto diario. Extremos horarios del acumulado pendientes de confirmar; TIME no se interpreta como inicio ni fin local.',
      'bounds':[[bottom,left],[top_y,right]],'imagen':f'data/wrf/{key}.png','raster':f'data/wrf/{key}.tif',
      'resolucion':'0.027 grados (aproximadamente 3 km)','leyenda':legend,'cantones':stats,
      'destacados':top,'criterio_destacados':'Mayor media territorial ponderada por area; solo cantones con cobertura completa y lluvia prevista positiva. Incluye empates.',
      'originales':[r.url]}
    save_json(folder/(key+'.json'),result)
    print('WRF guardado:',key,flush=True)
    return key

def dibujar_stretched(values,valid,path):
    # Rampa lineal minimo-maximo; punto central en (minimo+maximo)/2.
    sample=values[valid]
    if not sample.size:raise RuntimeError('Sin valores validos para la rampa.')
    media = float(sample.mean())
    desviacion = float(sample.std())
    low, high = media - 1.5 * desviacion, media + 1.5 * desviacion
    fraction=np.zeros(values.shape,dtype='float64')
    if high>low:fraction[valid]=(values[valid]-low)/(high-low)
    anchors=np.array([[40,146,199],[250,250,100],[232,16,20]],dtype='float64')
    rgba=np.zeros(values.shape+(4,),dtype='uint8')
    for channel in range(3):
        rgba[:,:,channel][valid]=np.rint(np.interp(fraction[valid],[0,.5,1],anchors[:,channel])).astype('uint8')
    rgba[:,:,3][valid]=255
    Image.fromarray(rgba).save(path)
    return {'tipo':'stretched','titulo':'Precipitación prevista',
        'colores':['#2892c7','#fafa64','#e81014'],
        'rangos':['Menor','Intermedia','Mayor'],
        'uniforme':high==low,'escala':'Lineal minimo-maximo por fecha; punto medio aritmetico.'}

def main():
    data=ROOT/'data';data.mkdir(exist_ok=True);session=requests.Session()
    session.headers['User-Agent']='Monitor-Los-Rios/1.0'
    init,times=capabilities(session)
    if not times:raise RuntimeError('Sin fechas para la inicializacion WRF publicada.')
    zones=json.loads(gzip.decompress((ROOT/'insumos/cantones.geojson.gz').read_bytes()))
    mask=json.loads((ROOT/'insumos/buffer.geojson').read_text());area=shape(mask['features'][0]['geometry'])
    completed=[];errors=[]
    for date in times:
        try:completed.append(process(session,init,date,zones,area))
        except Exception as exc:errors.append(str(exc));print('::warning::WRF '+str(exc),flush=True)
    if not completed:raise RuntimeError('Ninguna fecha WRF descargada: '+'; '.join(errors))
    # El selector ofrece solo la corrida actual; historicos quedan conservados.
    save_json(data/'estado_wrf.json',{'fuente':'WRF','estado':'parcial' if errors else 'completo',
      'intento':datetime.now(LOCAL).isoformat(),'inicializacion':init.isoformat(),'periodos':completed,
      'mensaje':'Pronostico diario; intervalo horario exacto pendiente de confirmar.'+(' Algunas fechas no disponibles.' if errors else '')})
if __name__=='__main__':main()
