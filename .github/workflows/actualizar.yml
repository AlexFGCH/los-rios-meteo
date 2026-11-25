name: Actualizar monitor de lluvia
on:
  workflow_dispatch:
  push:
    branches: [main]
  schedule:
    # Ecuador UTC-5: 11:17, 12:17, 13:17, 14:17, 15:17 y 16:17.
    - cron: '17 16-21 * * *'
permissions:
  contents: write
  pages: write
  id-token: write
concurrency:
  group: monitor-pages
  cancel-in-progress: false
jobs:
  actualizar:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
      - name: Instalar dependencias
        run: python -m pip install -r requirements.txt
      - name: Descargar y calcular IMERG
        env:
          NASA_PPS_EMAIL: ${{ secrets.NASA_PPS_EMAIL }}
        run: python scripts/actualizar_imerg.py
      - name: Guardar resultados completos y estado
        run: |
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git add data
          if ! git diff --cached --quiet; then
            git commit -m 'Actualizar precipitacion y estado de descarga'
            git push
          fi
      - name: Preparar pagina publica
        run: |
          mkdir -p dist
          cp index.html dist/
          cp -R data dist/
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: dist
      - name: Publicar monitor
        id: deploy
        uses: actions/deploy-pages@v4
