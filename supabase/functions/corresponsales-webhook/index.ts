// Programa de Corresponsales "Chivilcoy en Acción" — webhook de WhatsApp (Cloud API).
//
// Recibe los mensajes que los vecinos mandan al número del Diario, corre un formulario
// conversacional en 3 etapas (1: qué pasó → 2: nombre y apellido → 3: autorización) y, al aceptar,
// baja el video y lo deposita en la carpeta de Drive «videos notas actualidad» (en una
// subcarpeta con un contexto.txt). A partir de ahí, el desgrabador que ya existe arma la
// nota web + el reel (con la firma de corresponsal) y avisa al equipo para aprobar.
//
// Estado de la conversación + base de datos de colaboradores = tablas de Supabase
// (ver supabase/migrations/0001_corresponsales.sql). Todo gratis y 100% en la nube.
//
// Secrets que necesita (supabase secrets set ...):
//   WHATSAPP_TOKEN              token permanente de la API (System User)
//   WHATSAPP_PHONE_NUMBER_ID    id del número en la WABA
//   WHATSAPP_VERIFY_TOKEN       el que se pone también en el panel de Meta (verificación)
//   WHATSAPP_APP_SECRET         App Secret (para validar la firma de los webhooks)
//   GOOGLE_SA_JSON              JSON completo de la service account (una sola línea)
//   DRIVE_CORRESPONSALES_FOLDER_ID  id de la carpeta «videos notas actualidad» en Drive
//   (SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY los inyecta Supabase solo)

const GRAPH = "https://graph.facebook.com/v21.0";

const WA_TOKEN = Deno.env.get("WHATSAPP_TOKEN") ?? "";
const WA_PHONE_ID = Deno.env.get("WHATSAPP_PHONE_NUMBER_ID") ?? "";
const VERIFY_TOKEN = Deno.env.get("WHATSAPP_VERIFY_TOKEN") ?? "";
const APP_SECRET = Deno.env.get("WHATSAPP_APP_SECRET") ?? "";
// Acceso a Drive por OAuth de una cuenta de usuario (NO service account: las SA no tienen
// cuota y no pueden subir a un Drive normal). Se refresca un access token con estos 3 datos.
const G_CLIENT_ID = Deno.env.get("GOOGLE_OAUTH_CLIENT_ID") ?? "";
const G_CLIENT_SECRET = Deno.env.get("GOOGLE_OAUTH_CLIENT_SECRET") ?? "";
const G_REFRESH_TOKEN = Deno.env.get("GOOGLE_OAUTH_REFRESH_TOKEN") ?? "";
const DRIVE_FOLDER = Deno.env.get("DRIVE_CORRESPONSALES_FOLDER_ID") ?? "";
const SB_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SB_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

const LEGAL =
  "Autorizo al Diario La Campaña y Radio del Centro a publicar el material, sin obligación " +
  "de ningún tipo de retribución.";

// ── Utilidades ────────────────────────────────────────────────────────────────
function normalizar(s: string): string {
  // NFD + saca diacríticos combinantes (U+0300–U+036F) → "Pérez" ≈ "perez", "sí" → "si".
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();
}

function slug(s: string): string {
  return normalizar(s).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 30) || "corresponsal";
}

// La sesión junta varios archivos (1-5 fotos o 2-3 videos cortos) en el campo `media_id`, guardado
// como JSON [{id, tipo}]. `parseMedia` acepta eso o un id único viejo (compatibilidad).
function parseMedia(v: unknown): Array<{ id: string; tipo: string }> {
  const raw = String(v ?? "").trim();
  if (!raw) return [];
  if (raw.startsWith("[")) {
    try {
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter((m) => m && m.id).map((m) => ({ id: String(m.id), tipo: String(m.tipo || "video") })) : [];
    } catch { return []; }
  }
  return [{ id: raw, tipo: "video" }]; // id único viejo
}

// ── Supabase REST (con la service role key, saltea RLS) ───────────────────────
function sbHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}`, "Content-Type": "application/json", ...extra };
}

async function getSesion(waId: string): Promise<Record<string, unknown> | null> {
  const r = await fetch(
    `${SB_URL}/rest/v1/corresponsales_sesiones?wa_id=eq.${encodeURIComponent(waId)}&select=*`,
    { headers: sbHeaders() },
  );
  const rows = await r.json();
  return Array.isArray(rows) && rows.length ? rows[0] : null;
}

async function upsertSesion(row: Record<string, unknown>): Promise<void> {
  row.actualizado = new Date().toISOString();
  await fetch(`${SB_URL}/rest/v1/corresponsales_sesiones`, {
    method: "POST",
    headers: sbHeaders({ Prefer: "resolution=merge-duplicates,return=minimal" }),
    body: JSON.stringify(row),
  });
}

async function deleteSesion(waId: string): Promise<void> {
  await fetch(`${SB_URL}/rest/v1/corresponsales_sesiones?wa_id=eq.${encodeURIComponent(waId)}`, {
    method: "DELETE",
    headers: sbHeaders({ Prefer: "return=minimal" }),
  });
}

// Append ATÓMICO de un archivo a la sesión (RPC SQL con lock de fila). Reemplaza el patrón
// leer→append→upsert que perdía fotos cuando el álbum llegaba como webhooks concurrentes. Devuelve
// cuántos archivos hay tras ESTE append (1 = primer archivo del envío). Ver migración 0002.
async function addMediaAtomic(waId: string, id: string, tipo: string, perfil: string): Promise<number> {
  const r = await fetch(`${SB_URL}/rest/v1/rpc/corresponsales_add_media`, {
    method: "POST",
    headers: sbHeaders(),
    body: JSON.stringify({ p_wa_id: waId, p_id: id, p_tipo: tipo, p_perfil: perfil }),
  });
  if (!r.ok) {
    console.error(`addMediaAtomic FALLO ${r.status}: ${(await r.text()).slice(0, 200)}`);
    return 1; // que al menos mande la bienvenida
  }
  const total = await r.json();
  return typeof total === "number" && total > 0 ? total : 1;
}

async function registrarColaborador(waId: string, nombre: string, celular: string, autorizacion: string): Promise<void> {
  // Lee el registro actual para incrementar el contador (no hay upsert con +1 en REST).
  const r = await fetch(
    `${SB_URL}/rest/v1/corresponsales_colaboradores?wa_id=eq.${encodeURIComponent(waId)}&select=cant_notas`,
    { headers: sbHeaders() },
  );
  const rows = await r.json();
  const prev = Array.isArray(rows) && rows.length ? Number(rows[0].cant_notas ?? 0) : 0;
  await fetch(`${SB_URL}/rest/v1/corresponsales_colaboradores`, {
    method: "POST",
    headers: sbHeaders({ Prefer: "resolution=merge-duplicates,return=minimal" }),
    body: JSON.stringify({
      wa_id: waId, nombre, celular, autorizacion,
      ultima_vez: new Date().toISOString(), cant_notas: prev + 1,
    }),
  });
}

// ── WhatsApp Cloud API ────────────────────────────────────────────────────────
// Los celulares argentinos llegan como wa_id con un "9" extra (549 + área + número).
// Para RESPONDER hay que mandarlo SIN ese 9 (54 + área + número): Meta lo entrega igual al
// teléfono real (comprobado). Sin esto, el envío falla con «not in allowed list» / no entrega.
function normalizarAr(numero: string): string {
  const n = (numero || "").replace(/\D/g, "");
  if (n.startsWith("549") && n.length === 13) return "54" + n.slice(3);
  return n;
}

async function enviarTexto(to: string, body: string): Promise<void> {
  to = normalizarAr(to);
  const payload = JSON.stringify({ messaging_product: "whatsapp", to, type: "text", text: { body } });
  // Reintenta ante fallos transitorios de la Graph API (ej. 131005 intermitente) con backoff.
  for (let intento = 1; intento <= 3; intento++) {
    const r = await fetch(`${GRAPH}/${WA_PHONE_ID}/messages`, {
      method: "POST",
      headers: { Authorization: `Bearer ${WA_TOKEN}`, "Content-Type": "application/json" },
      body: payload,
    });
    if (r.ok) {
      console.log(`enviarTexto OK → ${to}`);
      return;
    }
    console.error(`enviarTexto intento ${intento}/3 FALLO ${r.status} → ${to}: ${(await r.text()).slice(0, 200)}`);
    if (intento < 3) await new Promise((res) => setTimeout(res, 1500 * intento));
  }
}

async function bajarMedia(mediaId: string): Promise<{ data: Uint8Array; mime: string }> {
  const meta = await (await fetch(`${GRAPH}/${mediaId}`, {
    headers: { Authorization: `Bearer ${WA_TOKEN}` },
  })).json();
  const bin = await fetch(meta.url, { headers: { Authorization: `Bearer ${WA_TOKEN}` } });
  return { data: new Uint8Array(await bin.arrayBuffer()), mime: meta.mime_type || "video/mp4" };
}

// ── Google Drive (service account: JWT RS256 → token → subir) ──────────────────
async function googleToken(): Promise<string> {
  // Refresca un access token de Drive con el refresh_token OAuth de la cuenta de usuario.
  const tok = await (await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: G_CLIENT_ID,
      client_secret: G_CLIENT_SECRET,
      refresh_token: G_REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  })).json();
  if (!tok.access_token) throw new Error("Google token: " + JSON.stringify(tok));
  return tok.access_token;
}

async function crearSubcarpeta(token: string, nombre: string): Promise<string> {
  const r = await fetch("https://www.googleapis.com/drive/v3/files?supportsAllDrives=true", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ name: nombre, mimeType: "application/vnd.google-apps.folder", parents: [DRIVE_FOLDER] }),
  });
  const j = await r.json();
  if (!j.id) throw new Error("Drive crear carpeta: " + JSON.stringify(j));
  return j.id;
}

async function subirArchivo(token: string, parentId: string, nombre: string, mime: string, data: Uint8Array): Promise<void> {
  const boundary = "diariocorresponsales" + crypto.randomUUID();
  const meta = JSON.stringify({ name: nombre, parents: [parentId] });
  const pre = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${meta}\r\n--${boundary}\r\nContent-Type: ${mime}\r\n\r\n`;
  const post = `\r\n--${boundary}--`;
  const enc = new TextEncoder();
  const body = new Uint8Array(enc.encode(pre).length + data.length + enc.encode(post).length);
  let o = 0;
  body.set(enc.encode(pre), o); o += enc.encode(pre).length;
  body.set(data, o); o += data.length;
  body.set(enc.encode(post), o);
  const r = await fetch(
    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": `multipart/related; boundary=${boundary}` },
      body,
    },
  );
  if (!r.ok) throw new Error("Drive subir " + nombre + ": " + (await r.text()).slice(0, 200));
}

// ── Finalizar: bajar el video y depositarlo en Drive ──────────────────────────
async function depositarEnDrive(s: Record<string, unknown>, waId: string): Promise<void> {
  const lista = parseMedia(s.media_id);
  if (!lista.length) throw new Error("No hay material para depositar.");
  const token = await googleToken();
  const fecha = new Date().toISOString().slice(0, 10);
  // El celular es el propio WhatsApp; el nombre = el que escribió el colaborador en la etapa 2
  // (cae al nombre del perfil de WhatsApp si por algún motivo no lo cargó).
  const nombre = String(s.nombre || s.perfil || "corresponsal");
  const celular = String(s.celular || waId);
  // Sufijo ÚNICO por envío: evita que dos envíos con el MISMO nombre (un corresponsal que manda
  // varios videos/fotos, o dos vecinos homónimos) choquen en el dedup-por-nombre del desgrabador
  // y se pierda uno en silencio. El nombre del corresponsal se conserva para identificarlo; el
  // dedup sigue protegiendo contra reprocesar el MISMO archivo (mismo nombre físico en Drive).
  const uniq = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const carpeta = await crearSubcarpeta(token, `corresponsal_${fecha}_${slug(nombre)}_${uniq}`);

  const contexto =
    `ORIGEN: corresponsal-whatsapp\n` +
    `NOMBRE: ${nombre}\n` +
    `CELULAR: ${celular}\n` +
    `DESCRIPCION: ${s.descripcion ?? ""}\n` +
    `AUTORIZACION: ACEPTADA — ${LEGAL} — ${new Date().toISOString()} — wa:${waId}\n`;

  // El contexto.txt va PRIMERO; el video ÚLTIMO (el disparador del desgrabador se fija en el
  // video, así arranca recién cuando ya están los dos archivos).
  const enc = new TextEncoder();
  await subirArchivo(token, carpeta, "contexto.txt", "text/plain; charset=UTF-8", enc.encode(contexto));
  // Bajar y subir CADA archivo, numerado (01, 02, …) para preservar el orden en el reel. Fotos → el
  // Apps Script dispara `--placa` (reel de la/s foto/s); videos → `--transcribe-video` (si hay varios,
  // el desgrabador los concatena). El contexto.txt va PRIMERO y el material DESPUÉS.
  let i = 0;
  for (const m of lista) {
    i++;
    const { data, mime } = await bajarMedia(String(m.id));
    const nn = String(i).padStart(2, "0");
    if (mime.startsWith("image/")) {
      const ext = mime.includes("png") ? "png" : (mime.includes("webp") ? "webp" : "jpg");
      await subirArchivo(token, carpeta, `foto_${slug(nombre)}_${uniq}_${nn}.${ext}`, mime, data);
    } else {
      const ext = mime.includes("quicktime") ? "mov" : "mp4";
      await subirArchivo(token, carpeta, `video_${slug(nombre)}_${uniq}_${nn}.${ext}`, mime, data);
    }
  }
}

// ── Máquina de estados ────────────────────────────────────────────────────────
async function manejarMensaje(msg: Record<string, any>, perfil: string): Promise<void> {
  const waId: string = msg.from;
  const tipo: string = msg.type;
  const texto: string = msg.text?.body ?? msg.button?.text ?? "";
  const esMedia = tipo === "video" || tipo === "image" ||
    (tipo === "document" && /^(video|image)\//.test(String(msg.document?.mime_type || "")));
  const sesion = await getSesion(waId);

  // "cancelar" en cualquier momento.
  if (sesion && normalizar(texto) === "cancelar") {
    await deleteSesion(waId);
    await enviarTexto(waId, "Listo, cancelé el envío. Cuando quieras, mandame de nuevo el material. 👋");
    return;
  }

  // Llega un video o foto. Se pueden mandar VARIOS y se juntan en un mismo envío. El append es
  // ATÓMICO (RPC), así que aunque el álbum llegue como varios webhooks CONCURRENTES no se pisan ni
  // se pierde ninguno (era el bug de "quedaba 1 sola foto en el reel"). `total` = cuántos hay tras
  // ESTE archivo (1 = el primero → bienvenida + formulario; >1 = adicional → ack corto).
  if (esMedia) {
    const mediaId = String((msg.video?.id) ?? (msg.image?.id) ?? (msg.document?.id) ?? "");
    const tipoM = msg.image ? "image"
      : (msg.video ? "video"
      : (String(msg.document?.mime_type || "").startsWith("image/") ? "image" : "video"));
    if (!mediaId) return;
    const total = await addMediaAtomic(waId, mediaId, tipoM, perfil);
    if (total <= 1) {
      await enviarTexto(waId,
        "¡Gracias por sumarte al *Programa de Corresponsales «Chivilcoy en Acción»* del Diario La " +
        "Campaña - Radio del Centro! 📣\n\nRecibí tu material. Podés mandar hasta *5 fotos*, o *1 video " +
        "de hasta 1 minuto*, o *2-3 videos de máximo 20 segundos* cada uno (van todos a un mismo reel). " +
        "Cuando termines, contame en *un solo mensaje* qué pasó:\n\n" +
        "• *Qué* ocurrió\n" +
        "• *Cuándo* fue\n" +
        "• *Dónde* fue\n" +
        "• *Cómo* pasó\n\n" +
        "(Si te arrepentís, escribí *cancelar*.)");
    } else {
      await enviarTexto(waId,
        `📎 Recibí ${total} archivos hasta ahora (van todos a un mismo reel). Podés mandar más, o ` +
        `contame *qué pasó* para continuar.`);
    }
    return;
  }

  // Sin sesión y sin media: responde a CUALQUIER mensaje con la bienvenida (pedido del usuario
  // 2026-08-06). Es una respuesta dentro de la ventana de servicio de 24 h → GRATIS.
  if (!sesion) {
    await enviarTexto(waId,
      "Bienvenido al programa de Corresponsales del Diario La Campaña - Radio del Centro. " +
      "Envianos un *video de hasta 1 minuto* (o hasta *3 videos de máximo 20 segundos* cada uno) o " +
      "*fotos*, y comenzamos a elaborar la noticia.");
    return;
  }

  // Hay sesión: avanzar según el paso.
  const paso = String(sesion.paso);
  if (paso === "hecho") {
    // ETAPA 1: guarda el relato del hecho (qué/cuándo/dónde/cómo) → es lo que redacta la nota/descripción.
    await upsertSesion({ wa_id: waId, paso: "datos", descripcion: texto.trim() });
    await enviarTexto(waId,
      "¡Gracias! 🙌 Ahora decime tu *nombre y apellido* (en un solo mensaje).\n\n" +
      "🔒 Tus datos (nombre, apellido, celular) serán *confidenciales* y no se publicarán: solo se " +
      "guardan en nuestra base de datos para dar el *premio del mes* al usuario que haya enviado el " +
      "material con mejores estadísticas en nuestras redes sociales.");
  } else if (paso === "datos") {
    // ETAPA 2: guarda el nombre y apellido del corresponsal (para la firma y la base de datos).
    await upsertSesion({ wa_id: waId, paso: "autorizacion", nombre: texto.trim() });
    await enviarTexto(waId,
      `Buenísimo. 📄 *Autorización*\n\n${LEGAL}\n\nSi estás de acuerdo, respondé *ACEPTO* para enviar el material.`);
  } else if (paso === "autorizacion") {
    const n = normalizar(texto);
    if (n === "acepto" || n === "si" || n.includes("acepto")) {
      await enviarTexto(waId, "¡Perfecto! Estoy guardando tu material… ⏳");
      try {
        await depositarEnDrive(sesion, waId);
        await registrarColaborador(waId, String(sesion.nombre ?? sesion.perfil ?? ""), waId,
          `ACEPTADA — ${new Date().toISOString()}`);
        await deleteSesion(waId);
        await enviarTexto(waId,
          "✅ ¡Listo! Tu material entró a *revisión editorial*. Si se publica, vas a sumar puntos " +
          "en el ranking mensual del Programa de Corresponsales. ¡Gracias por colaborar! 🙌");
      } catch (e) {
        console.error("depositar:", e);
        await enviarTexto(waId,
          "Uf, tuve un problema al guardar el material 😕. Por favor reenvialo para intentar de nuevo.");
        await deleteSesion(waId);
      }
    } else {
      await enviarTexto(waId,
        "Para poder usar el material necesito tu autorización. Respondé *ACEPTO* para continuar, " +
        "o *cancelar* para no enviarlo.");
    }
  }
}

// ── Verificación de la firma del webhook (X-Hub-Signature-256) ────────────────
async function firmaValida(req: Request, raw: string): Promise<boolean> {
  if (!APP_SECRET) return true; // sin secret configurado, no se valida (no recomendado)
  const firma = req.headers.get("x-hub-signature-256") || "";
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(APP_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(raw)));
  const hex = Array.from(mac).map((b) => b.toString(16).padStart(2, "0")).join("");
  return firma === `sha256=${hex}`;
}

// ── Entry point ───────────────────────────────────────────────────────────────
Deno.serve(async (req) => {
  const url = new URL(req.url);

  // Verificación inicial de Meta (GET).
  if (req.method === "GET") {
    if (url.searchParams.get("hub.verify_token") === VERIFY_TOKEN) {
      return new Response(url.searchParams.get("hub.challenge") ?? "", { status: 200 });
    }
    return new Response("forbidden", { status: 403 });
  }

  if (req.method !== "POST") return new Response("ok", { status: 200 });

  const raw = await req.text();
  if (!(await firmaValida(req, raw))) return new Response("bad signature", { status: 401 });

  // Respondemos 200 enseguida; el procesamiento sigue (Meta reintenta si no hay 200 rápido).
  try {
    const body = JSON.parse(raw);
    // Recorre TODOS los mensajes del webhook (antes solo se procesaba messages[0]: si Meta mandaba
    // un álbum como un único webhook con varios mensajes, se descartaban todas las fotos menos la
    // primera). Se procesan SECUENCIALMENTE (await en el loop) → sin carrera dentro de la misma
    // invocación; y para webhooks concurrentes, el append atómico (addMediaAtomic) protege igual.
    let algun = false;
    for (const entry of (body?.entry ?? [])) {
      for (const change of (entry?.changes ?? [])) {
        const value = change?.value;
        const perfil = value?.contacts?.[0]?.profile?.name ?? "";
        for (const msg of (value?.messages ?? [])) {
          algun = true;
          console.log(`MENSAJE de ${msg.from} tipo=${msg.type} texto="${msg.text?.body ?? ""}"`);
          try {
            await manejarMensaje(msg, perfil);
          } catch (e) {
            console.error("manejarMensaje:", e); // un mensaje que falla no frena a los demás
          }
        }
      }
    }
    if (!algun) console.log("POST sin mensajes (status/otro).");
  } catch (e) {
    console.error("webhook:", e);
  }
  return new Response("ok", { status: 200 });
});
