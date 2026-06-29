/**
 * Auditoría de permisos de Drive: lista quién tiene acceso a tus carpetas principales
 * (y flaggea las subcarpetas compartidas) y te manda el reporte por mail.
 *
 * Pegalo en el proyecto Apps Script "Desgrabador diario" (reusa _prop) y ejecutá
 * `auditarPermisos` una vez. La primera vez te pide permisos (Drive + enviar mail): aceptá.
 */
function _accesoDrive(f) {
  try { return String(f.getSharingAccess()); } catch (e) { return '??'; }
}

function _genteConAcceso(f, yo) {
  var out = [];
  try { f.getEditors().forEach(function (u) { var e = u.getEmail(); if (e && e !== yo) out.push(e + ' (editor)'); }); } catch (e) {}
  try { f.getViewers().forEach(function (u) { var e = u.getEmail(); if (e && e !== yo) out.push(e + ' (lector)'); }); } catch (e) {}
  return out;
}

function auditarPermisos() {
  var yo = Session.getActiveUser().getEmail();
  var objetivos = [], vistos = {};

  // Por ID (Script properties, si están)
  ['FOLDER_NUEVOS_ID', 'FOLDER_APROBADAS_ID', 'NOTAS_WEB_FOLDER_ID'].forEach(function (k) {
    var id = _prop(k);
    if (id) { try { objetivos.push(DriveApp.getFolderById(id)); } catch (e) {} }
  });
  // Por nombre (carpetas de Mi unidad)
  ['videos notas actualidad', 'notas para web', 'Diario', 'DIARIO PDF', 'Desgrabaciones'].forEach(function (nombre) {
    var it = DriveApp.getFoldersByName(nombre);
    while (it.hasNext()) objetivos.push(it.next());
  });

  var L = ['AUDITORÍA DE PERMISOS — ' + new Date().toLocaleString() + '\nTu cuenta: ' + yo + '\n'];
  objetivos.forEach(function (f) {
    if (vistos[f.getId()]) return; vistos[f.getId()] = true;
    var acc = _accesoDrive(f), g = _genteConAcceso(f, yo);
    var priv = (acc === 'PRIVATE' && g.length === 0);
    L.push((priv ? '🔒 PRIVADA   ' : '⚠️ COMPARTIDA') + '  «' + f.getName() + '»');
    L.push('     acceso general: ' + acc + (g.length ? '\n     con acceso: ' + g.join(', ') : ''));
    var subs = f.getFolders();
    while (subs.hasNext()) {
      var sf = subs.next();
      var acc2 = _accesoDrive(sf), g2 = _genteConAcceso(sf, yo);
      if (g2.length || (acc2 && acc2 !== 'PRIVATE' && acc2 !== '??')) {
        L.push('        ⚠️ subcarpeta «' + sf.getName() + '» — acceso ' + acc2 + (g2.length ? ' — ' + g2.join(', ') : ''));
      }
    }
    L.push('');
  });

  if (objetivos.length === 0) L.push('No encontré las carpetas principales (¿tienen otros nombres?).');
  var reporte = L.join('\n');
  Logger.log(reporte);
  try { MailApp.sendEmail(yo, 'Auditoría de permisos de tu Drive', reporte); } catch (e) { Logger.log('No se pudo mandar el mail: ' + e); }
}
