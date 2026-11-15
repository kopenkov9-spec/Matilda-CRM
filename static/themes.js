/* Applied before the page paints; preferences belong to this device. */
window.crmTheme = (() => {
  const key = 'mestno-appearance';
  const modes = {light:'Светла', dark:'Тъмна', system:'Според устройството'};
  const accents = {forest:['Гора',155], ocean:['Океан',213], plum:['Слива',275], copper:['Мед',28]};
  const media = matchMedia('(prefers-color-scheme: dark)');
  let prefs = {mode:'system', accent:'forest'};
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (saved && Object.hasOwn(modes,saved.mode)) prefs.mode = saved.mode;
    if (saved && Object.hasOwn(accents,saved.accent)) prefs.accent = saved.accent;
  } catch (_) {}
  function apply() {
    const dark = prefs.mode === 'dark' || (prefs.mode === 'system' && media.matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.accent = prefs.accent;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',dark?'#141a20':'#f5f6f2');
    document.querySelectorAll('[data-appearance]').forEach(input => {
      input.checked = prefs[input.dataset.appearance] === input.value;
    });
  }
  function panel() {
    return `<section class="panel appearance-panel"><div class="panel-head"><h2>Цветове и тема</h2><span class="pill">За това устройство</span></div><div class="panel-body"><p class="muted">Изборът се прилага веднага и се запомня в този браузър.</p><fieldset class="theme-options"><legend>Осветеност</legend><div class="theme-modes">${Object.entries(modes).map(([id,label])=>`<label class="theme-choice"><input type="radio" name="theme-mode" data-appearance="mode" value="${id}" ${prefs.mode===id?'checked':''}><span>${label}</span></label>`).join('')}</div></fieldset><fieldset class="theme-options"><legend>Цветова схема</legend><div class="theme-accents">${Object.entries(accents).map(([id,[label,hue]])=>`<label class="theme-choice theme-swatch" data-swatch="${id}"><input type="radio" name="theme-accent" data-appearance="accent" value="${id}" ${prefs.accent===id?'checked':''}><span class="swatch-preview" aria-hidden="true"><i></i><b></b><b></b></span><span>${label}</span></label>`).join('')}</div></fieldset></div></section>`;
  }
  document.addEventListener('change',event => {
    const input = event.target.closest('[data-appearance]');
    if (!input) return;
    const options = input.dataset.appearance === 'mode' ? modes : accents;
    if (!Object.hasOwn(options,input.value)) return;
    prefs[input.dataset.appearance] = input.value;
    apply();
    try { localStorage.setItem(key,JSON.stringify(prefs)); }
    catch (_) { if (typeof toast === 'function') toast('Темата е приложена, но браузърът не позволява запомняне.'); }
  });
  media.addEventListener('change',apply);
  window.addEventListener('storage',event => {
    if (event.key !== key) return;
    try {
      const saved = JSON.parse(event.newValue);
      if (saved && Object.hasOwn(modes,saved.mode) && Object.hasOwn(accents,saved.accent)) prefs = saved;
      else prefs = {mode:'system',accent:'forest'};
      apply();
    } catch (_) {}
  });
  apply();
  return {panel};
})();
