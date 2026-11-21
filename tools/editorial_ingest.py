#!/usr/bin/env python3
"""Carga editorial directa. Token solo en EDITORIAL_INGEST_TOKEN; sin dependencias externas."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAX_BODY = 32 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # El token de ingreso no se reenvía a otra URL, ni siquiera si el servidor redirige.
        return None


def save_receipt(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    os.replace(temporary, path)


def multipart(metadata, paths):
    boundary = 'editorial-' + uuid.uuid4().hex
    pieces = [f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
              'Content-Type: application/json; charset=utf-8\r\n\r\n'.encode(),
              json.dumps(metadata, ensure_ascii=False).encode(), b'\r\n']
    total = sum(len(piece) for piece in pieces)
    for number, path in enumerate(paths, 1):
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        mime = {'audio/x-wav': 'audio/wav', 'audio/x-m4a': 'audio/mp4'}.get(mime, mime)
        # Nunca se interpolan nombres del usuario en las cabeceras multipart.
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="archivo_{number:02d}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        if total + len(header) + path.stat().st_size + 2 > MAX_BODY:
            raise ValueError('El envío supera 32 MiB; no se comprimió ni envió ningún archivo.')
        data = path.read_bytes()
        pieces.extend([header, data, b'\r\n'])
        total += len(header) + len(data) + 2
    pieces.append(f'--{boundary}--\r\n'.encode())
    if total + len(pieces[-1]) > MAX_BODY:
        raise ValueError('El envío supera 32 MiB.')
    return b''.join(pieces), 'multipart/form-data; boundary=' + boundary


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recibo', required=True, type=Path, help='Archivo de recibo; reutilizarlo para consultar/reintentar el MISMO envío.')
    parser.add_argument('--estado', action='store_true', help='Consultar el recibo sin volver a subir material.')
    parser.add_argument('--nota', type=Path, help='Texto completo de la nota en UTF-8.')
    parser.add_argument('--archivo', action='append', type=Path, default=[], help='Repetir por cada foto, video o audio, en el orden de portada.')
    parser.add_argument('--lugar', default='')
    parser.add_argument('--habla', default='', help='Nombre y cargo de quien habla, si se adjuntan audios.')
    parser.add_argument('--fuente-url', default='', help='Referencia interna; el servidor no descarga esta URL.')
    parser.add_argument('--acepto', action='store_true', help='Autorizar el uso del material para este envío.')
    args = parser.parse_args(argv)
    endpoint = os.environ.get('EDITORIAL_INGEST_URL', '').rstrip('/')
    token = os.environ.get('EDITORIAL_INGEST_TOKEN', '')
    url = urllib.parse.urlsplit(endpoint)
    if (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1'))) or url.query or url.fragment or url.username or url.password:
        parser.error('Configurar EDITORIAL_INGEST_URL como URL HTTPS de la función (sin parámetros).')
    if len(token) < 32:
        parser.error('Configurar EDITORIAL_INGEST_TOKEN de al menos 32 caracteres en el entorno.')
    previous = json.loads(args.recibo.read_text(encoding='utf-8')) if args.recibo.exists() else None
    if previous and previous.get('endpoint') != endpoint:
        parser.error('El recibo corresponde a otro endpoint. Usar el destino original.')
    if args.estado and not previous:
        parser.error('No existe ese archivo de recibo.')
    identifier = previous['id'] if previous else str(uuid.uuid4())
    uuid.UUID(identifier)
    headers = {'Authorization': 'Bearer ' + token}
    if args.estado:
        request = urllib.request.Request(endpoint + '?id=' + identifier, headers=headers)
    else:
        if not args.acepto or not args.nota or not 1 <= len(args.archivo) <= 8:
            parser.error('Indicar --acepto, --nota y entre 1 y 8 --archivo.')
        metadata = {'descripcion': args.nota.read_text(encoding='utf-8'), 'lugar': args.lugar,
                    'habla': args.habla, 'fuente_url': args.fuente_url, 'autorizacion': 'ACEPTO'}
        body, content_type = multipart(metadata, args.archivo)
        headers.update({'Content-Type': content_type, 'Idempotency-Key': identifier})
        request = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
    # Se conserva el identificador ANTES de la red: un timeout nunca obliga a subir con otro id.
    save_receipt(args.recibo, {**(previous or {}), 'id': identifier, 'endpoint': endpoint})
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=130)
    except urllib.error.HTTPError as error:
        response = error
    except (TimeoutError, OSError) as error:
        print('No se confirmó el resultado. Consultar --estado con el mismo --recibo; no crear otro envío.', file=sys.stderr)
        return 2
    with response:
        result = json.loads(response.read(65536))
        status_code = response.status
    result.update({'id': identifier, 'endpoint': endpoint, 'http_status': status_code})
    save_receipt(args.recibo, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status_code < 400 and result.get('estado') == 'received' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (OSError, ValueError, KeyError) as error:
        print(f'No se completó la operación: {error}', file=sys.stderr)
        raise SystemExit(2)
