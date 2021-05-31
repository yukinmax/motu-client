import flask
from flask import request
import waitress
import call

app = flask.Flask('MOTU API')
app.config["DEBUG"] = True

@app.route('/', methods=['GET'])
def home():
    return '<h1>MOTU API</h1>'

@app.route('/api/v1/motu/mute-toggle', methods=['GET'])
def mute_toggle():
    if 'channel' in request.args:
        channel = int(request.args['channel'])
    else:
        return "Error: No channel field provided. Please specify channel."
    return call.toggle_channel_matrix(channel, 'mute')


if __name__ == "__main__":
    waitress.serve(app, port=5000)
