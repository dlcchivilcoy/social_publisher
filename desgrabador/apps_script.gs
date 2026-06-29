/**
 * Desgrabador audiovisual — disparador instantáneo desde Google Drive.
 *
 * Vive en la cuenta dlc.chivilcoy@gmail.com (script.google.com). Cada 1 minuto
 * revisa dos carpetas de Drive y, ante un video NUEVO, dispara el workflow
 * "Publicador Diario" de GitHub Actions vía workflow_dispatch:
 *
 *   - "videos notas actualidad"            → args: --transcribe-video --file <nombre> --uploader <email>
 *   - "videos notas actualidad/APROBADAS"  → args: --publish-video --file <nombre>
 *
 * CÓMO INSTALARLO:
 *   1. Entrá a https://script.google.com con la cuenta dlc.chivilcoy@gmail.com → Nuevo proyecto.
 *   2. Pegá este código.
 *   3. Project Settings → Script properties → agregá:
 *        GITHUB_PAT        = un fine-grained PAT con Actions: Read and write sobre el repo
 *                            dlcchivilcoy/social_publisher (igual que el de cron-job.org).
 *        FOLDER_NUEVOS_ID  = ID de la carpeta "videos notas actualidad".
 *        FOLDER_APROBADAS_ID = ID de la subcarpeta "APROBADAS".
 *      (El ID es lo que aparece en la URL de la carpeta en Drive, después de /folders/.)
 *   4. Ejecutá una vez `revisarNuevos` para autorizar permisos (Drive + conexiones externas).
 *   5. Triggers (reloj a la izquierda) → Add Trigger → función `revisarNuevos`,
 *      event source = Time-driven, Minutes timer, Every minute.
 */

var OWNER = 'dlcchivilcoy';
var REPO = 'social_publisher';
var WORKFLOW = 'publicador.yml';

function _prop(k) { return PropertiesService.getScriptProperties().getProperty(k); }

function _dispatch(args) {
  var url = 'https://api.github.com/repos/' + OWNER + '/' + REPO +
            '/actions/workflows/' + WORKFLOW + '/dispatches';
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'Authorization': 'Bearer ' + _prop('GITHUB_PAT'),
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify({ ref: 'main', inputs: { args: args } }),
    muteHttpExceptions: true
  });
  Logger.log('dispatch(' + args + ') → ' + res.getResponseCode());
}

function _vistos(key) {
  var raw = _prop(key);
  return raw ? JSON.parse(raw) : [];
}

function _marcarVisto(key, id) {
  var ids = _vistos(key);
  ids.push(id);
  if (ids.length > 500) ids = ids.slice(ids.length - 500);
  PropertiesService.getScriptProperties().setProperty(key, JSON.stringify(ids));
}

function _esVideo(file) {
  var mt = file.getMimeType() || '';
  if (mt.indexOf('video/') === 0) return true;
  var n = file.getName().toLowerCase();
  return /\.(mp4|mov|mkv|avi|webm|m4v|mpg|mpeg)$/.test(n);
}

// Junta todos los videos de una carpeta Y sus subcarpetas (para soportar que el
// colaborador mande una SUBCARPETA con video + fotos + texto). Saltea la subcarpeta
// excludeId (la de APROBADAS, que se escanea por separado).
function _recolectarVideos(folder, excludeId, acc) {
  var files = folder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    if (_esVideo(f)) acc.push(f);
  }
  var subs = folder.getFolders();
  while (subs.hasNext()) {
    var s = subs.next();
    if (excludeId && s.getId() === excludeId) continue;
    _recolectarVideos(s, excludeId, acc);
  }
  return acc;
}

function _procesarCarpeta(folderId, vistosKey, hacerArgs, excludeId) {
  if (!folderId) return;
  var folder = DriveApp.getFolderById(folderId);
  var vids = _recolectarVideos(folder, excludeId, []);
  var vistos = _vistos(vistosKey);
  var ahora = new Date().getTime();
  for (var i = 0; i < vids.length; i++) {
    var f = vids[i];
    if (vistos.indexOf(f.getId()) !== -1) continue;
    // Esperar a que el archivo esté completo: ignorar lo modificado hace <30s.
    if (ahora - f.getLastUpdated().getTime() < 30000) continue;
    var email = '';
    try { email = f.getOwner() ? f.getOwner().getEmail() : ''; } catch (e) {}
    _dispatch(hacerArgs(f.getName(), email));
    _marcarVisto(vistosKey, f.getId());
  }
}

// Nota-PLACA: una SUBCARPETA con Word/txt + foto pero SIN video.
function _carpetaEsPlaca(folder) {
  var hayDoc = false, hayFoto = false, hayVideo = false;
  var files = folder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    var n = f.getName().toLowerCase();
    var mt = f.getMimeType() || '';
    if (/\.(docx|txt)$/.test(n) || mt === 'application/vnd.google-apps.document') hayDoc = true;
    if (/\.(jpg|jpeg|png|webp|gif)$/.test(n) || mt.indexOf('image/') === 0) hayFoto = true;
    if (_esVideo(f)) hayVideo = true;
  }
  return hayDoc && hayFoto && !hayVideo;
}

function _procesarPlacas(folderId, vistosKey, excludeId, hacerArgs) {
  if (!folderId) return;
  var folder = DriveApp.getFolderById(folderId);
  var vistos = _vistos(vistosKey);
  var ahora = new Date().getTime();
  var subs = folder.getFolders();
  while (subs.hasNext()) {
    var sf = subs.next();
    if (excludeId && sf.getId() === excludeId) continue;
    if (vistos.indexOf(sf.getId()) !== -1) continue;
    if (!_carpetaEsPlaca(sf)) continue;
    // Esperar a que termine de subir: ignorar si algo se modificó hace <60s.
    var ultimo = 0, fl = sf.getFiles();
    while (fl.hasNext()) { var t = fl.next().getLastUpdated().getTime(); if (t > ultimo) ultimo = t; }
    if (ahora - ultimo < 60000) continue;
    _dispatch(hacerArgs(sf.getName()));
    _marcarVisto(vistosKey, sf.getId());
  }
}

function revisarNuevos() {
  var aprobadasId = _prop('FOLDER_APROBADAS_ID');
  // Etapa 1: videos nuevos (en la carpeta o en subcarpetas, menos APROBADAS) → desgrabar.
  _procesarCarpeta(_prop('FOLDER_NUEVOS_ID'), 'PROCESSED_NEW', function (name, email) {
    return '--transcribe-video --file "' + name + '" --uploader ' + (email || 'desconocido');
  }, aprobadasId);
  // Etapa 2: videos movidos a APROBADAS → publicar.
  _procesarCarpeta(aprobadasId, 'PROCESSED_APROBADAS', function (name, email) {
    return '--publish-video --file "' + name + '"';
  }, null);
  // FOTO-NOTA etapa 1: subcarpetas con Word + foto SIN video → borrador + mail para revisar.
  _procesarPlacas(_prop('FOLDER_NUEVOS_ID'), 'PROCESSED_PLACA', aprobadasId, function (name) {
    return '--placa --file "' + name + '"';
  });
  // FOTO-NOTA etapa 2: esas carpetas movidas a APROBADAS → publicar (web + foto a FB/IG).
  _procesarPlacas(aprobadasId, 'PROCESSED_PLACA_APROB', null, function (name) {
    return '--placa-publish --file "' + name + '"';
  });
}
