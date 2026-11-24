import sys
sys.path.insert(0, '.')
from app import app

with app.test_client() as client:
    resp = client.get('/')
    html = resp.get_data(as_text=True)

    # Check nav link
    idx = html.find('buysell')
    if idx >= 0:
        print('Found buysell at index', idx)
        print('Context:', html[max(0,idx-100):idx+100])
    else:
        print('buysell NOT found in HTML')

    # Check nav list
    nav_start = html.find('<nav class="main-nav"')
    if nav_start >= 0:
        nav_end = html.find('</nav>', nav_start)
        print('Nav HTML:', html[nav_start:nav_end+6])