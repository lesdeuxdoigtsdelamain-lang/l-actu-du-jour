"""Optional Commons photos; category illustration always exists on-device if no photo qualifies."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode,quote,urlsplit
from .fetch import FetchError
from .models import Image

class Clean(HTMLParser):
    def __init__(self):super().__init__();self.text=[]
    def handle_data(self,s):self.text.append(s)
def strip_html(s):
    c=Clean();c.feed(s);return re.sub(r'\s+',' ',' '.join(c.text)).strip()

# Avoid calling a random stock image an actual depiction of an event.
TERMS={'Monde':'earth globe nasa','Maroc':'Rabat Morocco skyline',
 'Monde musulman':'Islamic architecture mosque exterior',
 'Royaume-Uni':'London skyline city','France':'Paris city skyline',
 'Économie':'stock exchange buildings exterior','Science et technologie':'space telescope',
 'Santé':'hospital building exterior','Société':'city public street','Environnement':'forest nature landscape'}

def find(category,http):
    q=TERMS.get(category,'earth globe nasa')
    params={'action':'query','format':'json','formatversion':'2','generator':'search',
        'gsrsearch':'filetype:bitmap '+q,'gsrnamespace':'6','gsrlimit':'8',
        'prop':'imageinfo','iiprop':'url|mime|extmetadata','iiurlwidth':'600'}
    try:
        blob=http.get('https://commons.wikimedia.org/w/api.php?'+urlencode(params),limit=350000)
        pages=json.loads(blob).get('query',{}).get('pages',[])
    except (FetchError,ValueError,TypeError):return None
    for page in pages:
        try:
            info=page['imageinfo'][0]
            if info['mime'] not in ('image/jpeg','image/png','image/webp'):continue
            url=info.get('thumburl','')
            if urlsplit(url).hostname!='upload.wikimedia.org':continue
            metadata=info['extmetadata']
            licence=strip_html(metadata['LicenseShortName']['value'])
            if not re.match(r'^(CC BY(?:-SA)?\s|CC0\b|public domain\b|PD-)',licence,re.I):continue
            credit=strip_html(metadata.get('Artist',{}).get('value',''))[:130]
            if not credit:continue
            return Image(url=url,credit=credit,license=licence[:120],
                alt='Photographie d’illustration — '+category,
                page='https://commons.wikimedia.org/wiki/'+quote(page['title'].replace(' ','_'),safe=':_()-.'))
        except (KeyError,TypeError,ValueError):continue
    return None
