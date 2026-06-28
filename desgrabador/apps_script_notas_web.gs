/**
 * «Notas para web» — disparador instantáneo desde Google Drive.
 *
 * Pegá ESTAS funciones en el MISMO proyecto Apps Script del desgrabador (donde está
 * `revisarNuevos`), porque reusa `_prop`, `_dispatch`, `_vistos`, `_marcarVisto`.
 *
 * Qué hace: cada 1 minuto revisa la carpeta «notas para web». Cada SUBCARPETA = una nota
 * (Word + foto + opcional video). Cuando aparece una subcarpeta NUEVA y ya terminó de
 * subirse (tiene Word + foto y nada se modificó en los últimos 90 s), dispara UNA vez el
 * workflow de GitHub con `--notas-web`. Ese comando publica TODAS las notas pendientes de
 * una sola pasada, así que con un disparo alcanza aunque hayan subido varias juntas. Un
 * registro (Script properties + el ledger .notas_web.json del lado Python) evita repetir.
 *
 * INSTALACIÓN (1 sola vez):
 *   1. En el proyecto Apps Script, pegá este código en un archivo nuevo.
 *   2. Configuración del proyecto → Propiedades de la secuencia → agregá:
 *        NOTAS_WEB_FOLDER_ID = ID de la carpeta «notas para web» en Drive.
 *      (GITHUB_PAT ya está cargado del desgrabador; se reusa.)
 *   3. Ejecutá una vez `revisarNotasWeb` para autorizar permisos.
 *   4. Activador (reloj) → Agregar activador → función `revisarNotasWeb`,
 *      origen = Según tiempo, Temporizador por minutos, Cada minuto.
 */

// Margen para asegurarse de que el colaborador TERMINÓ de subir los archivos de la nota.
var NOTAS_WEB_SETTLE_MS = 90 * 1000;

function _archivoMasNuevo(folder) {
  var ultimo = 0;
  var files = folder.getFiles();
  while (files.hasNext()) {
    var t = files.next().getLastUpdated().getTime();
    if (t > ultimo) ultimo = t;
  }
  var subs = folder.getFolders();
  while (subs.hasNext()) {
    var sf = subs.next().getFiles();
    while (sf.hasNext()) {
      var t2 = sf.next().getLastUpdated().getTime();
      if (t2 > ultimo) ultimo = t2;
    }
  }
  return ultimo;
}

function _tieneWordYFoto(folder) {
  // Vale si hay TEXTO (un .docx subido, un .txt o un Google Doc) MÁS algo de media:
  // al menos una FOTO o un VIDEO (si no hay foto, la portada se saca de un frame del video).
  var hayDoc = false, hayMedia = false;
  var files = folder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    var n = f.getName().toLowerCase();
    var mt = f.getMimeType() || '';
    if (/\.(docx|txt)$/.test(n) || mt === 'application/vnd.google-apps.document') hayDoc = true;
    if (/\.(jpg|jpeg|png|webp|gif|mp4|mov|mkv|avi|webm|m4v|mpg|mpeg)$/.test(n)
        || mt.indexOf('image/') === 0 || mt.indexOf('video/') === 0) hayMedia = true;
    if (hayDoc && hayMedia) return true;
  }
  return false;
}

function revisarNotasWeb() {
  var folderId = _prop('NOTAS_WEB_FOLDER_ID');
  if (!folderId) { Logger.log('Falta NOTAS_WEB_FOLDER_ID'); return; }

  var base = DriveApp.getFolderById(folderId);
  var vistos = _vistos('PROCESSED_NOTAS_WEB');
  var ahora = new Date().getTime();
  var hayNuevo = false;

  var subs = base.getFolders();
  while (subs.hasNext()) {
    var sub = subs.next();
    var nombre = sub.getName();
    if (nombre.toUpperCase() === 'PUBLICADAS' || nombre.toUpperCase() === 'APROBADAS') continue;
    if (vistos.indexOf(sub.getId()) !== -1) continue;
    if (!_tieneWordYFoto(sub)) continue;
    if (ahora - _archivoMasNuevo(sub) < NOTAS_WEB_SETTLE_MS) continue;

    _marcarVisto('PROCESSED_NOTAS_WEB', sub.getId());
    hayNuevo = true;
    Logger.log('Nota web nueva lista: ' + nombre);
  }

  if (hayNuevo) _dispatch('--notas-web');
}
