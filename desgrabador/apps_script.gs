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

function _procesarCarpeta(folderId, vistosKey, hacerArgs) {
  if (!folderId) return;
  var folder = DriveApp.getFolderById(folderId);
  var files = folder.getFiles();
  var vistos = _vistos(vistosKey);
  var ahora = new Date().getTime();
  while (files.hasNext()) {
    var f = files.next();
    if (!_esVideo(f)) continue;
    if (vistos.indexOf(f.getId()) !== -1) continue;
    // Esperar a que el archivo esté completo: ignorar lo modificado hace <30s.
    if (ahora - f.getLastUpdated().getTime() < 30000) continue;
    var email = '';
    try { email = f.getOwner() ? f.getOwner().getEmail() : ''; } catch (e) {}
    _dispatch(hacerArgs(f.getName(), email));
    _marcarVisto(vistosKey, f.getId());
  }
}

function revisarNuevos() {
  // Etapa 1: videos nuevos → desgrabar y armar borrador.
  _procesarCarpeta(_prop('FOLDER_NUEVOS_ID'), 'PROCESSED_NEW', function (name, email) {
    return '--transcribe-video --file "' + name + '" --uploader ' + (email || 'desconocido');
  });
  // Etapa 2: videos movidos a APROBADAS → publicar.
  _procesarCarpeta(_prop('FOLDER_APROBADAS_ID'), 'PROCESSED_APROBADAS', function (name, email) {
    return '--publish-video --file "' + name + '"';
  });
}
