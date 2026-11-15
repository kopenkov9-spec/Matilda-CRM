window.crmLifecycle = (() => {
  let connection, checking = false, stopped = false;
  async function connect() {
    if (stopped || connection || checking || state.session?.role !== 'admin' || !['127.0.0.1','localhost','[::1]'].includes(location.hostname)) return;
    checking = true;
    try {
      const status = await api('/lifecycle/status');
      if (status.managed) connection = new EventSource('/api/lifecycle/desktop');
    } catch (_) {} finally { checking = false; }
  }
  function panel() {
    if (state.session?.role !== 'admin' || !['127.0.0.1','localhost','[::1]'].includes(location.hostname)) return '';
    return `<section class="panel"><div class="panel-head"><h2>Изключване</h2></div><div class="panel-body"><p class="muted">След затваряне на последния настолен раздел Матилда спира след 15 секунди. Phi, гласовата обработка и връзката с телефона също се изключват.</p><div class="spacer-top"><button class="btn danger" data-power-off>Изключи Матилда</button></div></div></section>`;
  }
  document.addEventListener('click',async event => {
    const button = event.target.closest('[data-power-off]');
    if (!button) return;
    if (state.recording || state.outbox.length || state.draft.trim() || state.audio) {
      toast('Първо изпратете или изчистете текущата бележка и неизпратените записи.');
      return;
    }
    button.disabled = true;
    try {
      await api('/lifecycle/shutdown',{method:'POST',body:{}});
      stopped = true;
      connection?.close();
      state.session = null;
      $('#app').innerHTML = '<div class="login"><h1>Матилда се изключва</h1><p>Локалните процеси спират. Можете да затворите този прозорец. За ново стартиране използвайте прекия път „Матилда“.</p></div>';
    } catch (error) { toast(error.message); button.disabled = false; }
  });
  window.addEventListener('pagehide',()=>{connection?.close();connection=null;});
  window.addEventListener('pageshow',connect);
  setInterval(connect,3000);
  return {panel};
})();
