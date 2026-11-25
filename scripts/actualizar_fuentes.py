"""Ejecuta cada fuente por separado y reconstruye el catalogo sin perder fechas."""
import json,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data';DATA.mkdir(exist_ok=True)
for key,script,statusfile in [('imerg','actualizar_imerg.py','estado.json'),('pdir','actualizar_pdir.py','estado_pdir.json'),('chirps','actualizar_chirps.py','estado_chirps.json')]:
    result=subprocess.run([sys.executable,'-u',str(ROOT/'scripts'/script)])
    if result.returncode:
        value={'estado':'error','fuente':key,'intento':datetime.now(timezone(timedelta(hours=-5))).isoformat(),
               'mensaje':'Error durante la actualizacion; se conserva el ultimo periodo completo. Revisar Actions.'}
        (DATA/statusfile).write_text(json.dumps(value,ensure_ascii=False,indent=2))
        print(f'::warning::La fuente {key} no pudo actualizarse. Se conserva su ultimo periodo disponible.',flush=True)
catalog={}
for key in ['imerg','pdir','chirps']:
    catalog[key]=sorted([p.stem for p in (DATA/key).glob('????-??-??.json')
                        if p.with_suffix('.png').exists() and p.with_suffix('.tif').exists()],reverse=True)
(DATA/'catalogo.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2))
print('Catalogo completo actualizado.',flush=True)
