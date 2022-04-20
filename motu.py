import urllib.request as req
import json


class DataStore():
    def __init__(self, hostname="ultralite-avb.local"):
        self.hostname = hostname

    def get(self, path):
        r = req.Request(
            'http://{}/datastore/{}'.format(
                self.hostname,
                path
            )
        )
        with req.urlopen(r) as f:
            return json.loads(f.read())

    def set(self, path, value):
        r = req.Request(
            'http://{}/datastore/{}'.format(
                self.hostname,
                path
            ),
            data='json={}'.format(json.dumps({'value': value})).encode(),
            method='PATCH'
        )
        with req.urlopen(r) as f:
            return f

    def toggle(self, path):
        try:
            s = self.get(path)['value']
        except KeyError:
            return "FAILURE"
        j = abs(s - 1)
        r = self.set(path, j)
        if r.code == 204:
            return int(j)
        else:
            return "FAILURE"
