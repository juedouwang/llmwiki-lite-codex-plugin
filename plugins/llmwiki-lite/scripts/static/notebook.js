/* Notebook storage adapter; continuous editing lives in document-editor.js. */
(() => {
  'use strict';
  const root = document.getElementById('notebook'); if (!root) return;
  if (!window.ResearchDocument || !document.getElementById('nb-source')) {
    const notice=document.createElement('p');notice.setAttribute('role','alert');notice.textContent='网页服务仍是旧版本，请重启本地服务后再编辑。';root.prepend(notice);return;
  }
  const {request, mount} = window.ResearchDocument, {project, note} = root.dataset;
  const base = `/api/project/${project}/notebook`, api = `${base}/${note}`;
  const normalize = data => ({...data, ...data.document, mode: data.readonly ? 'readonly' : 'draft'});
  const load = () => request(api + '?format=markdown').then(normalize);
  const payload = value => ({format: 'markdown', title: value.title, tags: value.tags, body: value.body, comments: value.comments});
  const formatTime = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) : '未知';
  mount({root:'notebook',prefix:'nb', key:`llmwiki-notebook:${project}:${note}`, load,
    async save(value, item) {
      const result = await request(api, {document: payload(value), revision: item.revision});
      history.replaceState(null, '', `/project/${project}/notebook/${note}`);
      return {...item, ...value, ...result.timestamps, revision: result.revision, exists: true};
    },
    async preview(body) { return (await request(base + '/preview', {text: body})).html; },
    async upload(data) { return '../assets/' + (await request(base + '/upload', {data})).image; },
    label: item => item?.mode === 'readonly' ? '只读笔记' : '',
    filename: item => (item?.title || '科研笔记').replace(/[<>:"/\\|?*]/g,'_') + '.md',
    info: item => [['创建时间',formatTime(item?.created_at)],['修改时间',formatTime(item?.updated_at)],['笔记 ID',note]],
    async history(ctx) {
      const list = await request(api + '/history');
      ctx.modal('版本历史',[ctx.node(list.versions.length ? '恢复历史会保留当前版本的快照。' : '还没有历史版本。')],list.versions.map(version => [formatTime(version.updated_at),async()=>{
        const previous = await request(api+'/history?format=markdown&revision='+version.revision);
        ctx.modal('历史版本',[ctx.node(previous.document.body,'pre'), ...previous.document.comments.map(c=>ctx.node('批注：'+c.text))],[['恢复为当前笔记',async()=>{
          if (!(await ctx.flush())) return;
          await request(api,{revision:ctx.current().revision,restore_revision:version.revision});
          await ctx.display(await load());ctx.close();
        }]]);
      }]));
    },
    async saveAs(ctx) {
      const id = crypto.randomUUID().replaceAll('-','');
      ctx.modal('另存为新笔记',[ctx.node('保留原笔记，新笔记仍属于当前项目。')],[['确认另存',async()=>{
        await request(`${base}/${id}`,{document:payload(ctx.content()),revision:''});
        // The copy is persisted; retaining this document's recovery draft is intentional.
        const link=ctx.node('打开新笔记','a');link.href=`/project/${project}/notebook/${id}`;link.target='_blank';link.rel='noopener';
        ctx.modal('已另存为新笔记',[ctx.node('原笔记没有改变。'),link]);
      }]]);
    }
  });
})();
