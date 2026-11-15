# Portafolio — Ewald Zamorano Valero

Sitio de una página conectado al perfil de Behance
[behance.net/ewaldzv](https://www.behance.net/ewaldzv). Los proyectos, las portadas y
las estadísticas se actualizan solos desde el perfil.

## Estructura

```
index.html                  Sitio completo (HTML + CSS + JS)
data/behance.json           Datos sincronizados (fuente de verdad)
data/behance.js             Los mismos datos, para cargar con <script> (funciona en file://)
assets/projects/<id>.webp   Portadas descargadas, nombradas por ID de proyecto
assets/img/avatar.jpg       Retrato usado en "Sobre mí"
scripts/sync_behance.py     Sincronizador con Behance (solo Python 3, sin dependencias)
.github/workflows/          Actualización automática diaria con GitHub Actions
```

## Cómo verlo

Abre `index.html` con doble clic. No necesita servidor ni instalar nada.

## Actualización automática

El flujo de trabajo `.github/workflows/sync-behance.yml` corre todos los días a las
08:00 UTC y también se puede lanzar a mano desde la pestaña **Actions** de GitHub:

1. Lee el feed público de Behance y la página del perfil.
2. Descarga las portadas nuevas y elimina las que ya no existen.
3. Regenera `data/behance.json` y `data/behance.js`.
4. Hace commit solo si hubo cambios.

Para activarlo:

1. Crea un repositorio en GitHub y sube el contenido de esta carpeta como raíz.
2. En **Settings → Actions → General**, permite que los workflows escriban en el
   repositorio (*Read and write permissions*).
3. Publica con **GitHub Pages** (Settings → Pages → rama `main`) o conecta el
   repositorio a **Netlify/Vercel**. Cada commit del bot vuelve a desplegar el sitio.

Si publicas con Netlify, lo más simple es conectar el repositorio (Netlify despliega
automáticamente con cada commit). Netlify Drop, en cambio, es una copia manual y no
recibe las actualizaciones del bot.

## Sincronizar a mano

```bash
python3 scripts/sync_behance.py
```

No requiere `pip install`: usa solo la biblioteca estándar de Python 3.9+.
Cada ejecución imprime cuántos proyectos encontró y qué portadas descargó.

## Cómo funciona

- **Proyectos y portadas**: feed RSS público de Behance
  (`behance.net/feeds/user?username=ewaldzv`). Las imágenes se piden en 808 px WebP.
- **Estadísticas, colores y experiencia**: se leen de la página pública del perfil.
  Si Behance cambia el HTML y esa lectura falla, el script conserva las estadísticas
  anteriores y no rompe la sincronización.
- **Categorías**: se deducen del título (`Video`, `Stand`, `Inmobiliario`, `Campaña`
  o `Proyecto`). Los filtros y contadores se generan solos en el navegador.
- **Orden**: el proyecto más reciente aparece primero y ocupa la tarjeta destacada.
- **Límite**: el feed entrega los últimos proyectos publicados. Si algún día tienes
  más de los que aparecen, se pueden agregar a mano en `data/behance.json`.

## Personalizar

- **Textos, sección "Sobre mí", contacto y redes**: se editan directamente en
  `index.html`.
- **Categorías**: se asignan en `scripts/sync_behance.py` (función `category_for`).
- **Títulos cortos**: función `short_title` del mismo script.
- **Colores del sitio**: variables CSS al inicio de `index.html` (`--accent`, etc.).

No edites `data/behance.js` a mano: se sobrescribe en cada sincronización. Si necesitas
corregir algo puntual, hazlo en `data/behance.json` o mejor en el script.

## Publicar

- **GitHub Pages**: sube la carpeta a un repositorio y activa Pages en Settings.
- **Netlify / Vercel**: conecta el repositorio; el despliegue es automático.
- **Netlify Drop** (rápido, sin repo): arrastra la carpeta a <https://app.netlify.com/drop>.
  Sin repositorio no hay actualización automática.

Cuando tengas dominio propio, actualiza `<link rel="canonical">` y las etiquetas `og:`
del `<head>` con la URL final.

## Nota sobre las imágenes

Las portadas se descargan desde el CDN de Behance para que el sitio cargue rápido y
funcione sin conexión. Los derechos de cada pieza son de sus autores y clientes.
