import os
import urllib.request, urllib.parse, json, sys

# Login to CASX
data = urllib.parse.urlencode({'username': 'admin', 'password': os.environ.get('CASX_ADMIN_PASSWORD', '')}).encode()
req = urllib.request.Request('http://' + os.environ.get('CASX_HOST', '127.0.0.1:5115') + '/api/auth/login', data=data, 
    headers={'Content-Type': 'application/x-www-form-urlencoded'})
token = json.loads(urllib.request.urlopen(req, timeout=10).read())['access_token']

# Test cloudsaver login
data = json.dumps({'username': 'admin', 'password': os.environ.get('CLOUDSAVER_ADMIN_CODE', '')}).encode()
req = urllib.request.Request('http://' + os.environ.get('CLOUDSAVER_HOST', '127.0.0.1:8008') + '/api/user/login', data=data,
    headers={'Content-Type': 'application/json'})
try:
    resp = urllib.request.urlopen(req, timeout=10)
    d = json.loads(resp.read())
    print('CLOUDSAVER: success=' + str(d.get('success')))
except Exception as e:
    print('CLOUDSAVER: FAIL - ' + str(e))

# Full suggestions response
print()
req = urllib.request.Request('http://' + os.environ.get('CASX_HOST', '127.0.0.1:5115') + '/api/tasks/suggestions?q=测试&d=0',
    headers={'Authorization': 'Bearer ' + token})
resp = urllib.request.urlopen(req, timeout=30)
d = json.loads(resp.read())
print(json.dumps(d, indent=2, ensure_ascii=False)[:1500])
