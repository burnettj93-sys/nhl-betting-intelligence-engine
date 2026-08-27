import json, re, sys, hashlib

def extract(tool_result_path):
    with open(tool_result_path) as f:
        data = json.load(f)
    text = data[0]['text']
    text = re.sub(r'\n\n\(captured at origin.*\)\s*$', '', text, flags=re.S)
    raw = json.loads(text)
    return raw

if __name__ == '__main__':
    tool_result_path = sys.argv[1]
    out_path = sys.argv[2]
    raw = extract(tool_result_path)
    with open(out_path, 'w') as f:
        f.write(raw)
    h = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    print('bytes:', len(raw.encode('utf-8')))
    print('chars:', len(raw))
    print('sha256:', h)
    print('first 500 chars:')
    print(raw[:500])
