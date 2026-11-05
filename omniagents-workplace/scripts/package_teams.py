import argparse
import json
from pathlib import Path
import struct
from urllib.parse import urlsplit
from uuid import UUID, uuid5
import zipfile
import zlib


def icon(size, transparent):
    rows = []
    for vertical in range(size):
        row = bytearray([0])
        for horizontal in range(size):
            position = horizontal / size
            height = vertical / size
            strokes = ((0.20, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 0.8))
            white = any(left <= position <= right and abs(height - (0.28 + (0.43 * (position - left) / (right - left) if index % 2 == 0 else 0.43 * (right - position) / (right - left)))) < 0.035 for index, (left, right) in enumerate(strokes))
            row.extend((255, 255, 255, 255) if white else (0, 0, 0, 0) if transparent else (49, 46, 129, 255))
        rows.append(bytes(row))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--privacy-url', required=True)
    parser.add_argument('--terms-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('origin must be an HTTPS origin')
    for value in [args.privacy_url, args.terms_url]:
        if urlsplit(value).scheme != 'https' or not urlsplit(value).hostname:
            parser.error('policy URLs must be HTTPS')
    client = str(UUID(args.client_id))
    manifest = {
        '$schema': 'https://developer.microsoft.com/en-us/json-schemas/teams/v1.22/MicrosoftTeams.schema.json',
        'manifestVersion': '1.22', 'version': '1.0.0', 'id': str(uuid5(UUID(client), 'workplace-teams')),
        'developer': {'name': 'Workplace', 'websiteUrl': origin, 'privacyUrl': args.privacy_url, 'termsOfUseUrl': args.terms_url},
        'name': {'short': 'Workplace', 'full': 'Workplace for Microsoft Teams'},
        'description': {'short': 'Your organization’s Workplace, inside Teams.', 'full': 'Open your existing Workplace with your Microsoft account. Access remains controlled by your Workplace administrator.'},
        'icons': {'outline': 'outline.png', 'color': 'color.png'}, 'accentColor': '#312E81',
        'staticTabs': [{'entityId': 'workplace', 'name': 'Workplace', 'contentUrl': origin + '/?host=teams', 'websiteUrl': origin, 'scopes': ['personal']}],
        'validDomains': [parsed.netloc],
        'webApplicationInfo': {'id': client, 'resource': 'api://' + client},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        archive.writestr('color.png', icon(192, False))
        archive.writestr('outline.png', icon(32, True))
    print(args.output)


if __name__ == '__main__':
    main()
