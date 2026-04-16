(async () => {
   try {
      const res = await fetch('/version/full', { cache: 'no-store' });
      if (!res.ok) return;
      const { version, changelog } = await res.json();
      const el = document.getElementById('changelog');
      el.innerHTML = Object.entries(changelog)
         .map(([v, items]) => {
            const tag = v === version ? '<span class="current">(current)</span>' : '';
            return `<div class="version-block"><h2>v${v} ${tag}</h2><ul>${items.map(c => `<li>${c}</li>`).join('')}</ul></div>`;
         })
         .join('');
   } catch (err) {
      console.error('Changelog load error:', err);
   }
})();
