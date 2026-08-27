import json, re, sys

def extract(tool_result_path, out_dir):
    with open(tool_result_path) as f:
        data = json.load(f)
    text = data[0]['text']
    text = re.sub(r'\n\n\(captured at origin.*\)\s*$', '', text, flags=re.S)
    arr = json.loads(text)
    saved = []
    for item in arr:
        parsed = json.loads(item)
        # date key: first day in gameWeek
        date_key = parsed['gameWeek'][0]['date']
        out_path = f'{out_dir}/{date_key}.json'
        with open(out_path, 'w') as out:
            out.write(item)  # write the RAW untouched text, byte for byte
        saved.append((date_key, len(item), len(parsed['gameWeek'])))
    return saved

if __name__ == '__main__':
    tool_result_path = sys.argv[1]
    out_dir = sys.argv[2]
    for date_key, length, ndays in extract(tool_result_path, out_dir):
        print(date_key, length, ndays)
