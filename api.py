from flask import Flask, request
import json
import waitress
import motu

app = Flask('MOTU API')
app.config["DEBUG"] = True
motu_ds = motu.DataStore()


@app.route('/', methods=['GET'])
def home():
    return '<h1>MOTU API</h1>'


@app.route('/api/v1/motu/mute-toggle', methods=['GET'])
def mute_toggle():
    if 'bus' not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if 'index' not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args['bus'])
    channel = int(request.args['index'])
    path = 'mix/{}/{}/matrix/mute'.format(bus, channel)
    return json.dumps({'status': str(motu_ds.toggle(path))})


@app.route('/api/v1/motu/mute-status', methods=['GET'])
def mute_status():
    if 'bus' not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if 'index' not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args['bus'])
    channel = int(request.args['index'])
    path = 'mix/{}/{}/matrix/mute'.format(bus, channel)
    return json.dumps({'status': str(motu_ds.get(path))})


if __name__ == "__main__":
    waitress.serve(app, port=5000)
