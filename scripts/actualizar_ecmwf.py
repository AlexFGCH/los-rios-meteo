"""ECMWF IFS Open Data 0.1 grados, corrida 12 UTC, cinco dias siguientes.
Seis tp acumulados 0-24..0-144 h; cinco diferencias 24-48..120-144 h.
Cada periodo es 07h00-07h00 Ecuador. No incluye el dia de inicializacion.
"""
import gzip,json,math,os,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import requests,numpy as np,rasterio,eccodes as ec
from rasterio.transform import from_origin
from rasterio.features import geometry_mask
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from shapely.strtree import STRtree
from pyproj import Transformer
from actualizar_imerg import save_json
from actualizar_wrf import dibujar_stretched
ROOT=Path(__file__).resolve().parents[1]
LOCAL=timezone(timedelta(hours=-5));NODATA=-9999.;STEPS=(24,48,72,96,120,144)
SOURCES=('https://data.ecmwf.int/forecasts','https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com')

def iso(d):return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
def url_archivo(base,date,step):return f'{base}/{date}/12z/ifs/0p1/oper/{date}120000-{step}h-oper-fc-sfc'
def obtener(session,url,offset=None,length=None):
    for attempt in range(3):
        try:
            headers={'Accept-Encoding':'identity'}
            if offset is not None:headers['Range']=f'bytes={offset}-{offset+length-1}'
            with session.get(url,headers=headers,stream=True,timeout=(20,120)) as r:
                r.raise_for_status()
                if offset is not None:
                    prefix=f'bytes {offset}-{offset+length-1}/'
                    if r.status_code!=206 or not r.headers.get('Content-Range','').startswith(prefix):
                        raise RuntimeError('El servidor no respeta Range; no se descarga el archivo GRIB completo.')
                    data=r.raw.read(length+1)
                    if len(data)!=length:raise RuntimeError('Bloque tp incompleto.')
                else:
                    data=r.raw.read(4*1024*1024+1)
                    if len(data)>4*1024*1024:raise RuntimeError('Indice demasiado grande.')
                return data
        except requests.HTTPError as exc:
            if exc.response.status_code in (400,401,403,404) or attempt==2:raise
        except (requests.RequestException,RuntimeError):
            if attempt==2:raise
        time.sleep(2*(attempt+1))

def indice_tp(session,base,date,step):
    url=url_archivo(base,date,step)
    rows=[json.loads(line) for line in obtener(session,url+'.index').decode().splitlines() if line.strip()]
    found=[r for r in rows if r.get('param')=='tp' and r.get('levtype')=='sfc' and str(r.get('step'))==str(step) and r.get('type')=='fc']
    if len(found)!=1:raise RuntimeError(f'Se esperaba un unico tp para +{step} horas.')
    row=found[0]
    if str(row.get('date'))!=date or int(row.get('time'))!=1200 or row.get('stream')!='oper':raise RuntimeError('Indice de otra corrida o producto.')
    if int(row['_offset'])<0 or not 20<=int(row['_length'])<=100*1024*1024:raise RuntimeError('Rango tp fuera de los limites esperados.')
    return url,row

def elegir_corrida(session,now):
    fixed=os.environ.get('ECMWF_FECHA_CORRIDA','').strip()
    if fixed:datetime.strptime(fixed,'%Y%m%d')
    dates=[fixed] if fixed else [(now.date()-timedelta(days=i)).strftime('%Y%m%d') for i in range(2)]
    for date in dates:
        for base in SOURCES:
            try:
                indices={p:indice_tp(session,base,date,p) for p in STEPS}
                return datetime.strptime(date,'%Y%m%d').replace(hour=12,tzinfo=timezone.utc),base,indices
            except Exception as exc:print(f'ECMWF {date} 12 UTC no completo en {base}: {exc}',flush=True)
    raise RuntimeError('No hay corrida completa 12 UTC de 0.1 grados para hoy o ayer; se conservan resultados anteriores.')

def validar_grib(data):
    if not (len(data)>=20 and data[:4]==b'GRIB' and data[7]==2 and int.from_bytes(data[8:16],'big')==len(data) and data[-4:]==b'7777'):
        raise RuntimeError('El bloque no es un unico mensaje GRIB2 completo.')

def leer_region(data,run,step,extent):
    validar_grib(data);gid=ec.codes_new_from_message(data)
    try:
        get=lambda key:ec.codes_get(gid,key)
        if str(get('centre')) not in ('ecmf','98') or get('shortName')!='tp' or get('stepType')!='accum':raise RuntimeError('No es tp acumulado ECMWF.')
        if int(get('dataDate'))!=int(run.strftime('%Y%m%d')) or int(get('dataTime'))!=1200:raise RuntimeError('GRIB de otra corrida.')
        if int(get('startStep'))!=0 or int(get('endStep'))!=step or ec.codes_get_long(gid,'stepUnits')!=1:raise RuntimeError('Se esperaba un acumulado desde inicio hasta el plazo, expresado en horas.')
        valid=run+timedelta(hours=step)
        if int(get('validityDate'))!=int(valid.strftime('%Y%m%d')) or int(get('validityTime'))!=1200:raise RuntimeError('Hora valida GRIB incompatible.')
        unit=str(get('units')).lower().replace(' ','').replace('[','').replace(']','')
        if unit in ('m','mofwater','mofwaterequivalent'):factor=1000.
        elif unit in ('mm','kgm**-2','kgm-2','kg/m^2','kg/m2'):factor=1.
        else:raise RuntimeError('Unidad de precipitacion desconocida: '+unit)
        if get('gridType')!='regular_ll':raise RuntimeError('No es una grilla geografica regular.')
        dx=abs(float(get('iDirectionIncrementInDegrees')));dy=abs(float(get('jDirectionIncrementInDegrees')))
        if not np.isclose(dx,.1,atol=1e-8) or not np.isclose(dy,.1,atol=1e-8):raise RuntimeError('Cambio la resolucion; se esperaba 0.1 grados.')
        if int(get('jPointsAreConsecutive')) or int(get('alternativeRowScanning')):raise RuntimeError('Orden de escaneo GRIB no soportado.')
        ni,nj=int(get('Ni')),int(get('Nj'))
        lons=(float(get('longitudeOfFirstGridPointInDegrees'))+np.arange(ni)*dx*(-1 if int(get('iScansNegatively')) else 1)+180)%360-180
        lats=float(get('latitudeOfFirstGridPointInDegrees'))+np.arange(nj)*dy*(1 if int(get('jScansPositively')) else -1)
        xmin,ymin,xmax,ymax=extent
        cols=np.flatnonzero((lons+dx/2>xmin)&(lons-dx/2<xmax));rows=np.flatnonzero((lats+dy/2>ymin)&(lats-dy/2<ymax))
        if not len(cols) or not len(rows):raise RuntimeError('El limite no intersecta el GRIB.')
        cols=cols[np.argsort(lons[cols])];rows=rows[np.argsort(-lats[rows])]
        if len(cols)>1 and not np.allclose(np.diff(lons[cols]),dx,atol=1e-7):raise RuntimeError('Columnas regionales no contiguas.')
        full=ec.codes_get_values(gid)
        if full.size!=ni*nj:raise RuntimeError('Numero de valores GRIB inesperado.')
        values=full.reshape(nj,ni)[np.ix_(rows,cols)].astype('float64');del full
        if int(get('bitmapPresent')):values[values==float(get('missingValue'))]=np.nan
        values[(values<0)|~np.isfinite(values)]=np.nan
        grid=from_origin(float(lons[cols[0]])-dx/2,float(lats[rows[0]])+dy/2,dx,dy)
        if xmin<grid.c-1e-6 or xmax>grid.c+values.shape[1]*dx+1e-6 or ymax>grid.f+1e-6 or ymin<grid.f-values.shape[0]*dy-1e-6:raise RuntimeError('Cobertura regional incompleta.')
        return values*factor,grid,str(get('units'))
    finally:ec.codes_release(gid)

def diferencia_mm(current,previous):
    valid=np.isfinite(current)&np.isfinite(previous)
    result=current-previous
    minimum=float(result[valid].min()) if valid.any() else 0.
    if minimum<-.1:raise RuntimeError(f'Diferencia negativa {minimum:.6f} mm: no se publica.')
    adjusted=int(np.sum(valid&(result<0)))
    if adjusted:print(f'ECMWF: {adjusted} negativos pequenos por redondeo ajustados a cero; minimo {minimum:.6f} mm.',flush=True)
    return np.where(valid,np.maximum(result,0),np.nan),adjusted

def preparar_pesos(zones,grid,size):
    project=Transformer.from_crs(4326,32717,always_xy=True).transform
    cells=[]
    for row,col in np.ndindex(size):
        left,top=grid*(col,row);right,bottom=grid*(col+1,row+1)
        cells.append(transform(project,box(left,bottom,right,top)))
    tree=STRtree(cells);out=[]
    for f in zones['features']:
        geom=transform(project,shape(f['geometry']));indices=[];weights=[]
        for index in tree.query(geom,predicate='intersects'):
            overlap=geom.intersection(cells[int(index)]).area
            if overlap>0:indices.append(int(index));weights.append(overlap)
        out.append((f['properties'],np.array(indices,dtype=int),np.array(weights),geom.area))
    return out

def estadisticas(values,weights):
    flat=values.ravel();stats=[];ranked=[]
    for props,indices,areas,total_area in weights:
        sample=flat[indices];ok=np.isfinite(sample)&(sample>=0)
        area=float(areas[ok].sum());coverage=min(1.,area/total_area) if total_area else 0.
        mean=float(np.dot(sample[ok],areas[ok])/area) if area and coverage>=.999 else None
        maximum=float(sample[ok].max()) if area and coverage>=.999 else None
        item={**props,'cobertura_completa':coverage>=.999,'cobertura_valida_pct':round(100*coverage,2),
          'media_mm':round(mean,2) if mean is not None else None,'maxima_mm':round(maximum,2) if maximum is not None else None}
        stats.append(item)
        if mean is not None:ranked.append((mean,item))
    ranked.sort(key=lambda p:(-p[0],str(p[1]['codigo'])))
    previous=None;rank=0
    for i,(mean,item) in enumerate(ranked,1):
        if previous is None or not np.isclose(mean,previous,rtol=0,atol=1e-9):rank=i
        item['posicion_relativa']=rank;previous=mean
    eligible=[item for mean,item in ranked if mean>0 and str(item.get('codigo','')).isdigit() and len(str(item['codigo']))==4]
    top=[]
    if eligible:
        threshold=eligible[min(2,len(eligible)-1)]['posicion_relativa'];top=[x for x in eligible if x['posicion_relativa']<=threshold]
    return stats,top

def publicar_dia(folder,run,step,values,grid,area,weights,source,units,adjusted):
    end=run+timedelta(hours=step);start=end-timedelta(hours=24)
    key=run.strftime('%Y%m%dT%H%MZ')+'_'+end.strftime('%Y%m%dT%H%MZ')
    valid=np.isfinite(values)&(values>=0);inside=geometry_mask([mapping(area)],out_shape=values.shape,transform=grid,invert=True);display=valid&inside
    if not display.any():raise RuntimeError('ECMWF sin celdas validas en el limite.')
    with rasterio.open(folder/(key+'.tif'),'w',driver='GTiff',width=values.shape[1],height=values.shape[0],count=1,dtype='float32',crs='EPSG:4326',transform=grid,nodata=NODATA,compress='lzw') as out:
        out.write(np.where(display,values,NODATA).astype('float32'),1);out.set_band_unit(1,'mm')
        out.update_tags(fuente=source,corrida_utc=iso(run),inicio=start.astimezone(LOCAL).isoformat(),fin=end.astimezone(LOCAL).isoformat(),plazo_inicial=str(step-24),plazo_final=str(step),grid_grados='0.1',unidad_grib=units)
    legend=dibujar_stretched(values,display,folder/(key+'.png'))
    legend.update(rangos=['Baja','Media','Alta'],titulo='Precipitación prevista',escala='Desviacion estandar 1.5 por fecha; clases relativas, no categorias oficiales de amenaza.')
    stats,top=estadisticas(values,weights)
    left,top_y=grid*(0,0);right,bottom=grid*(values.shape[1],values.shape[0])
    result={'tipo':'pronostico','producto':'ecmwf','modelo':'ECMWF IFS','fuente':'ECMWF IFS · ECMWF Open Data (CC BY 4.0)',
      'estadisticas_csv_version':1,'fecha_fin':end.astimezone(LOCAL).date().isoformat(),'fecha_servicio':iso(end),'inicializacion':iso(run),
      'inicio':start.astimezone(LOCAL).isoformat(),'fin':end.astimezone(LOCAL).isoformat(),'horas_acumuladas':24,
      'convencion_temporal':'07h00 a 07h00 Ecuador (UTC-5); diferencia de acumulados desde la misma corrida 12 UTC.',
      'generado':datetime.now(LOCAL).isoformat(),'intervalos':1,'unidad':'mm','bounds':[[bottom,left],[top_y,right]],
      'imagen':f'data/ecmwf/{key}.png','raster':f'data/ecmwf/{key}.tif','resolucion':'0.1 grados (aproximadamente 11 km)',
      'leyenda':legend,'cantones':stats,'destacados':top,'criterio_destacados':'Mayor media territorial ponderada por area; incluye empates.',
      'metodo_media':'Ponderada por area de interseccion con celdas nativas; solo cobertura completa.',
      'metodo_maxima':'Mayor valor de celda nativa con interseccion de area positiva; solo cobertura completa.',
      'paso_inicial_horas':step-24,'paso_final_horas':step,'negativos_pequenos_ajustados':adjusted,'fuente_descarga':source,'licencia':'CC BY 4.0'}
    save_json(folder/(key+'.json'),result);return key

def main():
    now=datetime.now(LOCAL);session=requests.Session();session.headers['User-Agent']='LosRios-ECMWF/3.0'
    run,source,indices=elegir_corrida(session,now)
    stale=run.astimezone(LOCAL).date()!=now.date()
    print('ECMWF corrida seleccionada: '+iso(run)+(' (corrida anterior; revisar vigencias)' if stale else ''),flush=True)
    data=ROOT/'data';folder=data/'ecmwf';folder.mkdir(parents=True,exist_ok=True)
    keys=[run.strftime('%Y%m%dT%H%MZ')+'_'+(run+timedelta(hours=p)).strftime('%Y%m%dT%H%MZ') for p in STEPS[1:]]
    state={'fuente':'ECMWF IFS','estado':'completo','intento':now.isoformat(),'inicializacion':iso(run),'corrida_anterior':stale,'periodos':keys,
      'mensaje':'Cinco periodos 07h00-07h00 Ecuador.'+(' Se usa la corrida completa anterior; comprobar vigencias.' if stale else '')}
    if all((folder/(k+ext)).exists() for k in keys for ext in ('.json','.png','.tif')):
        save_json(data/'estado_ecmwf.json',state);print('ECMWF: cinco periodos de esta corrida ya disponibles.',flush=True);return
    zones=json.loads(gzip.decompress((ROOT/'insumos/cantones.geojson.gz').read_bytes()))
    area=shape(json.loads((ROOT/'insumos/buffer.geojson').read_text())['features'][0]['geometry'])
    if not (-83<area.bounds[0]<area.bounds[2]<-73 and -7<area.bounds[1]<area.bounds[3]<4):raise RuntimeError('Limite fuera de Ecuador; revisar coordenadas.')
    cache=ROOT/'.cache/ecmwf'/run.strftime('%Y%m%dT%H%MZ');cache.mkdir(parents=True,exist_ok=True)
    staging=cache/'salidas';staging.mkdir(exist_ok=True);previous=None;reference=None;weights=None;produced=[]
    for step in STEPS:
        url,row=indices[step];path=cache/f'tp_{step}h.grib2'
        if path.exists() and path.stat().st_size==int(row['_length']):raw=path.read_bytes()
        else:
            raw=obtener(session,url+'.grib2',int(row['_offset']),int(row['_length']))
            validar_grib(raw);temp=path.with_suffix('.part');temp.write_bytes(raw);temp.replace(path)
        current,grid,unit=leer_region(raw,run,step,area.bounds)
        print(f'ECMWF tp +{step}h verificado; unidad original {unit}.',flush=True)
        if reference is None:reference=(current.shape,grid);weights=preparar_pesos(zones,grid,current.shape)
        elif current.shape!=reference[0] or not np.allclose(tuple(grid),tuple(reference[1]),atol=1e-8,rtol=0):raise RuntimeError('Los acumulados usan grillas distintas.')
        if previous is not None:
            daily,adjusted=diferencia_mm(current,previous)
            produced.append(publicar_dia(staging,run,step,daily,grid,area,weights,source,unit,adjusted))
        previous=current
    if produced!=keys or len(produced)!=5:raise RuntimeError('No se completaron cinco periodos de una misma corrida.')
    # Solo hacer visible la nueva corrida cuando los cinco productos estan listos.
    for key in keys:
        for extension in ('.tif','.png','.json'):(staging/(key+extension)).replace(folder/(key+extension))
    save_json(data/'estado_ecmwf.json',state)
    print('ECMWF: cinco periodos completos publicados.',flush=True)
if __name__=='__main__':main()
