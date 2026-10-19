"""In-memory Google Drive boundary for isolated tests only; never a real upload."""
import hashlib

class Reply:
    def __init__(self,fn):self.fn=fn
    def execute(self):return self.fn()
    def next_chunk(self):return None,self.fn()


class Drive:
    def __init__(self):
        self.items={'parent':{'id':'parent','name':'Movie Clips','mimeType':'application/vnd.google-apps.folder',
            'capabilities':{'canAddChildren':True}}};self.created=0;self.media_updates=0
        self.fail_create=False;self.fail_transfer=False;self.corrupt=False
    def files(self):return self
    def about(self):return self
    def get(self,**kw):
        if 'fileId' not in kw:return Reply(lambda:{'storageQuota':{'limit':str(10**12),'usage':'0'}})
        return Reply(lambda:dict(self.items[kw['fileId']]))
    def list(self,**kw):
        def run():
            import re
            q=kw['q'];items=list(self.items.values())
            if "'parent' in parents" in q:items=[r for r in items if 'parent' in r.get('parents',[])]
            if 'appProperties' in q:
                match=re.findall(r"key='([^']+)' and value='([^']+)'",q)
                items=[r for r in items if all(r.get('appProperties',{}).get(k)==v for k,v in match)]
            if "name = '" in q:
                name=q.split("name = '")[1].split("'")[0];items=[r for r in items if r['name']==name]
            if "name='channel-manifest.json'" in q:items=[r for r in items if r['name']=='channel-manifest.json']
            return {'files':items}
        return Reply(run)
    def _write(self,item,media):
        if media:
            data=media.getbytes(0,media.size());item.update(size=str(len(data)),md5Checksum=hashlib.md5(data).hexdigest(),bytes=data)
    def create(self,**kw):
        def run():
            self.created+=1;fid='file'+str(self.created)
            item={'id':fid,'size':'0',**kw['body']};self.items[fid]=item;self._write(item,kw.get('media_body'))
            if self.fail_create and item.get('appProperties'):
                self.fail_create=False;raise TimeoutError('creation response lost')
            return dict(item)
        return Reply(run)
    def update(self,**kw):
        def run():
            self.media_updates+=1;item=self.items[kw['fileId']];self._write(item,kw.get('media_body'))
            if self.corrupt:item['md5Checksum']='bad'
            if self.fail_transfer:
                self.fail_transfer=False;raise TimeoutError('transfer response lost')
            return dict(item)
        return Reply(run)


