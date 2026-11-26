"""Ejecuta cada fuente por separado y reconstruye el catalogo sin perder fechas."""
import json,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data';DATA.mkdir(exist_ok=True)
def horario_habilitado(fuente, ahora):
    hora_ecuador=ahora.astimezone(timezone(timedelta(hours=-5))).hour
    inicio={'pdir':6,'imerg':11}.get(fuente,0)
    return hora_ecuador>=inicio

for key,script,statusfile in [('imerg','actualizar_imerg.py','estado.json'),('pdir','actualizar_pdir.py','estado_pdir.json'),('chirps','actualizar_chirps.py','estado_chirps.json'),('wrf','actualizar_wrf.py','estado_wrf.json')]:
    if not horario_habilitado(key,datetime.now(timezone.utc)):
        print(f'{key}: fuera del horario de consulta; se conserva el ultimo periodo completo.',flush=True)
        continue
    result=subprocess.run([sys.executable,'-u',str(ROOT/'scripts'/script)])
    if result.returncode:
        previous=json.loads((DATA/statusfile).read_text()) if (DATA/statusfile).exists() else {}
        value={**previous,'estado':'error','fuente':key,'intento':datetime.now(timezone(timedelta(hours=-5))).isoformat(),
               'mensaje':'Error durante la actualizacion; se conserva el ultimo periodo completo. Revisar Actions.'}
        (DATA/statusfile).write_text(json.dumps(value,ensure_ascii=False,indent=2))
        print(f'::warning::La fuente {key} no pudo actualizarse. Se conserva su ultimo periodo disponible.',flush=True)
catalog={}
for key in ['imerg','pdir','chirps','wrf']:
    catalog[key]=sorted([p.stem for p in (DATA/key).glob('*.json')
                        if p.with_suffix('.png').exists() and p.with_suffix('.tif').exists()],reverse=True)
state=DATA/'estado_wrf.json'
if state.exists():
    wrf=json.loads(state.read_text())
    if wrf.get('periodos'):catalog['wrf']=[p for p in wrf['periodos'] if p in catalog['wrf']]
(DATA/'catalogo.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2))
print('Catalogo completo actualizado.',flush=True)
