/**
 * Desgrabador — WEB APP de los botones del mail (Aprobar / Corregir).
 *
 * IMPORTANTE: pegá estas funciones en el MISMO proyecto Apps Script "Desgrabador diario"
 * (donde está `revisarNuevos`), porque reusa _prop, _dispatch, _marcarVisto.
 *
 * Script properties que hay que tener (las 3 primeras ya están; agregá las de Wix):
 *   GITHUB_PAT, FOLDER_NUEVOS_ID, FOLDER_APROBADAS_ID   (ya cargadas)
 *   WIX_API_KEY, WIX_SITE_ID                            (NUEVAS, para el botón Corregir)
 *
 * DESPLIEGUE:
 *   Implementar → Nueva implementación → tipo "Aplicación web".
 *   Ejecutar como: Yo (dlc.chivilcoy)  |  Quién tiene acceso: Solo yo.
 *   Copiá la URL que termina en /exec y ponela en el .env del repo como
 *   APPROVE_WEBAPP_URL=... (y en el secret ENV_FILE). Listo: los mails traen los botones.
 */

function _html(msg) {
  return HtmlService.createHtmlOutput(
    '<div style="font-family:Arial;max-width:600px;margin:40px auto;font-size:18px;color:#222">'
    + msg + '</div>');
}

function _esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _buscarPorNombre(folder, name) {
  var it = folder.getFilesByName(name);
  if (it.hasNext()) return it.next();
  var subs = folder.getFolders();
  while (subs.hasNext()) {
    var r = _buscarPorNombre(subs.next(), name);
    if (r) return r;
  }
  return null;
}

function _wixHeaders() {
  return { 'Authorization': _prop('WIX_API_KEY'), 'wix-site-id': _prop('WIX_SITE_ID'),
           'Content-Type': 'application/json' };
}

function _getDraft(id) {
  var r = UrlFetchApp.fetch('https://www.wixapis.com/blog/v3/draft-posts/' + id,
    { method: 'get', headers: _wixHeaders(), muteHttpExceptions: true });
  if (r.getResponseCode() >= 300) return null;
  return JSON.parse(r.getContentText()).draftPost;
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.token !== _prop('WEBAPP_TOKEN')) return _html('Acceso no autorizado.');
  if (p.action === 'approve') {
    var name = p.name || '';
    var nuevos = DriveApp.getFolderById(_prop('FOLDER_NUEVOS_ID'));
    var aprob = DriveApp.getFolderById(_prop('FOLDER_APROBADAS_ID'));
    var f = _buscarPorNombre(nuevos, name);
    if (!f) return _html('⚠️ No encontré el video «' + _esc(name) + '». ¿Ya lo aprobaste o lo borraste?');
    f.moveTo(aprob);
    _marcarVisto('PROCESSED_APROBADAS', f.getId()); // que el trigger no lo re-dispare
    _dispatch('--publish-video --file "' + name + '"');
    return _html('✅ <b>Aprobado.</b> Publicando «' + _esc(name) + '» en la web y como reel en Facebook e Instagram. '
      + 'En un par de minutos ya está online.');
  }
  if (p.action === 'edit') {
    var d = _getDraft(p.draft || '');
    if (!d) return _html('⚠️ No encontré el borrador para editar.');
    var texto = '';
    (d.richContent && d.richContent.nodes ? d.richContent.nodes : []).forEach(function (n) {
      if (n.type === 'PARAGRAPH' && n.nodes) {
        n.nodes.forEach(function (t) { if (t.textData) texto += t.textData.text; });
        texto += '\n\n';
      }
    });
    var form = '<div style="font-family:Arial;max-width:680px;margin:30px auto">'
      + '<h2>Corregir la nota</h2>'
      + '<form method="post" action="">'
      + '<input type="hidden" name="draft" value="' + _esc(p.draft) + '">'
      + '<input type="hidden" name="token" value="' + _esc(p.token) + '">'
      + '<b>Título</b><br><input name="title" style="width:100%;font-size:16px;padding:6px" value="' + _esc(d.title) + '"><br><br>'
      + '<b>Texto</b><br><textarea name="texto" style="width:100%;height:320px;font-size:15px;padding:6px">' + _esc(texto.trim()) + '</textarea><br><br>'
      + '<button type="submit" style="background:#e2620c;color:#fff;border:0;padding:12px 22px;font-size:16px;border-radius:6px;cursor:pointer">Guardar cambios</button>'
      + '</form><p style="color:#666">Tras guardar, volvé al mail y tocá «Aprobar y publicar».</p></div>';
    return HtmlService.createHtmlOutput(form);
  }
  if (p.action === 'delete') {
    // Borra (papelera) una nota del blog por su id. Para el botón «Borrar de la web».
    var id = p.post || '';
    if (!id) return _html('No se indicó qué nota borrar.');
    var r = UrlFetchApp.fetch('https://www.wixapis.com/blog/v3/draft-posts/' + id,
      { method: 'delete', headers: _wixHeaders(), muteHttpExceptions: true });
    if (r.getResponseCode() < 300) return _html('🗑️ <b>Nota borrada de la web.</b>');
    return _html('No se pudo borrar (' + r.getResponseCode() + '): ' + _esc(r.getContentText().slice(0, 200)));
  }
  if (p.action === 'preview') {
    // Reproduce el reel DENTRO del navegador (sin descargarlo). Recibe la URL del mp4
    // (asset del GitHub Release) y la mete en un <video>; así el navegador lo reproduce
    // en vez de bajarlo (que es lo que pasa al abrir el link directo).
    var u = String(p.url || '');
    if (!/^https:\/\//.test(u)) return _html('⚠️ No hay un video para previsualizar.');
    var page = '<div style="background:#000;min-height:100vh;display:flex;align-items:center;'
      + 'justify-content:center;margin:0"><video src="' + _esc(u) + '" controls autoplay '
      + 'playsinline style="max-width:100%;max-height:100vh"></video></div>';
    return HtmlService.createHtmlOutput(page)
      .setTitle('Previsualizar video')
      .addMetaTag('viewport', 'width=device-width, initial-scale=1');
  }
  return _html('Desgrabador: acción no reconocida.');
}

function doPost(e) {
  var p = (e && e.parameter) || {};
  if (p.token !== _prop('WEBAPP_TOKEN')) return _html('Acceso no autorizado.');
  var d = _getDraft(p.draft || '');
  if (!d) return _html('⚠️ No encontré el borrador.');
  // Conserva la foto y el video; reemplaza solo los párrafos de texto.
  var keep = (d.richContent && d.richContent.nodes ? d.richContent.nodes : [])
    .filter(function (n) { return n.type === 'IMAGE' || n.type === 'VIDEO'; });
  var paras = String(p.texto || '').split(/\n\n+/).filter(function (s) { return s.trim(); })
    .map(function (s, i) {
      return { type: 'PARAGRAPH', id: 'p' + i, nodes: [{ type: 'TEXT', id: '', textData: { text: s, decorations: [] } }] };
    });
  var body = {
    draftPost: { title: p.title, richContent: { nodes: keep.concat(paras) }, media: d.media },
    fieldMask: ['title', 'richContent', 'media']
  };
  var r = UrlFetchApp.fetch('https://www.wixapis.com/blog/v3/draft-posts/' + p.draft,
    { method: 'patch', headers: _wixHeaders(), payload: JSON.stringify(body), muteHttpExceptions: true });
  if (r.getResponseCode() < 300) {
    return _html('✅ <b>Texto corregido.</b> Volvé al mail y tocá «Aprobar y publicar» para que salga.');
  }
  return _html('⚠️ No se pudo guardar (' + r.getResponseCode() + '): ' + _esc(r.getContentText().slice(0, 200)));
}
