"""Simbologia provincial para los mapas descargables de precipitacion.
Solo IMERG, PDIR y CHIRPS. No modifica TIFF, estadisticas ni mapas de toda el area.
"""
import gzip,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from shapely.geometry import shape,mapping,box
from shapely.ops import unary_union
from shapely.prepared import prep
from PIL import Image
import jenkspy
ROOT=Path(__file__).resolve().parents[1]
VERSION=1
SOURCES=('imerg','pdir','chirps')
COLORS=[(40,146,199),(160,194,155),(250,250,100),(250,141,52),(232,16,20)]

def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp.json');tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8');tmp.replace(path)

def classify(values,valid,path):
    sample=np.round(values[valid].astype('float64'),3)
    if sample.size==0:raise RuntimeError('Los Rios no tiene celdas validas.')
    count=min(5,len(np.unique(sample)))
    breaks=[float(x) for x in jenkspy.jenks_breaks(sample,n_classes=count)]
    positions=np.round(np.linspace(0,4,count)).astype(int) if count>1 else np.array([0])
    colors=[COLORS[i] for i in positions]
    classes=np.searchsorted(breaks[1:-1],np.round(values,3),side='left')
    rgba=np.zeros(values.shape+(4,),dtype='uint8')
    for i,color in enumerate(colors):rgba[(classes==i)&valid]=(*color,255)
    Image.fromarray(rgba).save(path)
    labels=[(f'{breaks[i]:.2f}–{breaks[i+1]:.2f}' if i==0 else f'>{breaks[i]:.2f}–{breaks[i+1]:.2f}') for i in range(count)]
    return {'metodo':'Natural Breaks (Jenks)','categorias':count,'categorias_solicitadas':5,
      'titulo':'Acumulado estimado (mm)','ambito':'Provincia de Los Rios',
      'rangos':labels,'limites':breaks,'colores':['#%02x%02x%02x'%c for c in colors]}

def positive_overlap_mask(geometry,grid,size,valid):
    # Incluye celdas de borde con area positiva, no solo centros de celda.
    candidate=geometry_mask([mapping(geometry)],out_shape=size,transform=grid,invert=True,all_touched=True)&valid
    prepared=prep(geometry)
    for row,col in np.argwhere(candidate):
        left,top=grid*(int(col),int(row));right,bottom=grid*(int(col)+1,int(row)+1)
        cell=box(left,bottom,right,top)
        if not prepared.covers(cell) and geometry.intersection(cell).area<=0:candidate[row,col]=False
    return candidate

def generate(source,path,province,geometry_hash):
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('tipo')=='pronostico' or data.get('unidad')!='mm':raise RuntimeError('Producto incompatible para '+str(path))
    tif=ROOT/data['raster']
    signature=hashlib.sha256(tif.read_bytes()+geometry_hash.encode()+json.dumps({'version':VERSION,'generado':data.get('generado'),'bounds':data['bounds']},sort_keys=True).encode()).hexdigest()
    folder=ROOT/'data/exportacion/losrios'/source;folder.mkdir(parents=True,exist_ok=True)
    target=folder/path.name;image=target.with_suffix('.png')
    if target.exists() and image.exists():
        existing=json.loads(target.read_text())
        if existing.get('firma')==signature:return False
    with rasterio.open(tif) as src:
        if src.count!=1 or src.crs.to_epsg()!=4326 or src.transform.b!=0 or src.transform.d!=0:raise RuntimeError('Grilla no compatible con recorte provincial.')
        values=src.read(1,masked=True);valid=~np.ma.getmaskarray(values)&np.isfinite(values.data)&(values.data>=0)
        local=positive_overlap_mask(province,src.transform,values.shape,valid)
        bounds=[[src.bounds.bottom,src.bounds.left],[src.bounds.top,src.bounds.right]]
        if not np.allclose(bounds,data['bounds'],atol=1e-7):raise RuntimeError('Los limites del TIFF y del producto no coinciden.')
        temp=image.with_suffix('.tmp.png');legend=classify(values.data,local,temp)
        sample=values.data[local];minimum=float(sample.min());maximum=float(sample.max())
    # Los maximos de la tabla deben concordar con el raster; jamas se sustituyen.
    stats=[s.get('maxima_mm') for s in data.get('cantones',[]) if s.get('provincia')=='LOS RÍOS' and isinstance(s.get('maxima_mm'),(float,int))]
    table_max=max(stats) if stats else None
    if table_max is not None and abs(maximum-table_max)>.011:
        raise RuntimeError(f'Maximo provincial {maximum} distinto de tabla {table_max}; revisar grilla y limites.')
    result={'version':VERSION,'firma':signature,'fuente':source,'ambito':'losrios','unidad':'mm',
      'original_generado':data.get('generado'),'generado':datetime.now(timezone.utc).isoformat(),
      'imagen':image.relative_to(ROOT).as_posix(),'bounds':bounds,'leyenda':legend,
      'minima_mm':round(minimum,3),'maxima_mm':round(maximum,3),'maxima_tabla_mm':table_max,'celdas_validas':int(local.sum()),
      'metodo_mascara':'Celdas nativas validas con interseccion de area positiva con los 13 cantones originales de Los Rios.'}
    temp.replace(image);save_json(target,result)
    print(f'Los Rios {source} {path.stem}: {minimum:.2f}–{maximum:.2f} mm, {int(local.sum())} celdas.',flush=True)
    return True

def main():
    raw=(ROOT/'insumos/cantones.geojson.gz').read_bytes();zones=json.loads(gzip.decompress(raw))
    chosen=[shape(f['geometry']) for f in zones['features'] if f['properties'].get('provincia')=='LOS RÍOS']
    if len(chosen)!=13:raise RuntimeError('Se esperaban los 13 cantones originales de Los Rios.')
    province=unary_union(chosen);geometry_hash=hashlib.sha256(raw).hexdigest()
    catalog=json.loads((ROOT/'data/catalogo.json').read_text())
    failures=[];updated=0
    for source in SOURCES:
        for key in catalog.get(source,[]):
            try:updated+=generate(source,ROOT/'data'/source/(key+'.json'),province,geometry_hash)
            except Exception as exc:failures.append(f'{source} {key}: {exc}');print('::warning::Rangos provinciales '+failures[-1],flush=True)
    print(f'Rangos de Los Rios: {updated} periodos actualizados; {len(failures)} errores.',flush=True)
    if failures:raise RuntimeError('No se completaron los rangos provinciales: '+'; '.join(failures))
if __name__=='__main__':main()
