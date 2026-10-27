// Reduced from live UARB grid + Download Files markup, with overlapping virtual pages.
const retrievalMode = '__DOWNLOAD_MODE__';
document.addEventListener('click', event => {
  const button = event.target.closest('.fm_object_276 button, .fm_object_195 button');
  if (!button) return;
  if (retrievalMode === 'same_tab_no_reset' && button.closest('.fm-selected')) return;
  if (retrievalMode === 'active_tab_reload' && button.closest('.fm-selected')) {
    setTimeout(() => { document.querySelector('tbody').innerHTML = ''; }, 10);
    return;
  }
  const labels = [...button.closest('.fm_object_276, .fm_object_195').querySelectorAll('button')].map(item => item.textContent);
  const category = button.textContent.split(' - ')[0];
  const total = Number(button.textContent.split(' - ')[1]);
  let start = 0;
  document.body.innerHTML = `<div class="fm_object_195">${labels.map(label =>
    `<div class="${label === button.textContent ? 'fm-selected' : ''}"><button>${label}</button></div>`).join('')}</div>
    <button>GO GET IT</button><div class="iwps_fm-list-container"><div class="v-grid">
    <div class="v-grid-scroller-vertical" style="height:100px;overflow:auto;width:20px">
      <div style="height:${retrievalMode === 'early_end' ? 10 : 3000}px"></div></div>
    <table role="grid" aria-rowcount="${total}"><tbody></tbody></table></div></div>`;
  const tbody = document.querySelector('tbody');
  function render() {
    const end = Math.min(start + 3, retrievalMode === 'early_end' ? 2 : total);
    const markup = Array.from({length: Math.max(0, end - start)}, (_, offset) => {
      const index = ['duplicate_rows', 'ambiguous_record'].includes(retrievalMode) ? 0 : start + offset;
      const id = retrievalMode === 'duplicate_ids' ? '1000' : retrievalMode === 'special_id' ? `H.${index + 1}+(A)` :
        (category === 'Exhibits' ? `H-${index + 1}` : `${1000 + index}`);
      return `<tr class="v-grid-row-has-data" role="row" data-index="${index}">
        <td><div class="fm-textarea hand-cursor"><div class="text">${id}</div></div>
        <div class="fm_object_202"><div class="text">${retrievalMode === 'changed_title' ? 'Changed ' : ''}Document ${index + 1}</div></div>
        ${retrievalMode === 'missing_date' ? '' : `<div class="fm_object_203"><div class="text">${retrievalMode === 'blank_date' ? '' : retrievalMode === 'changed_date' ? '02/01/2026' : `01/${String(index + 1).padStart(2, '0')}/2026`}</div></div>`}
        <button>Preview</button><button class="get-file">GO GET IT</button></td></tr>`;
    }).join('');
    if (retrievalMode === 'delayed_fields') {
      tbody.innerHTML = '<tr class="v-grid-row-has-data" role="row"><td>Loading fields</td></tr>';
      setTimeout(() => { tbody.innerHTML = markup; }, 80);
    } else {
      tbody.innerHTML = markup;
    }
  }
  render();
  let lastScroll = 0;
  document.querySelector('.v-grid-scroller-vertical').addEventListener('scroll', event => {
    if (retrievalMode === 'stuck_grid') return;
    start = event.target.scrollTop < lastScroll ? 0 : start + 2;
    lastScroll = event.target.scrollTop;
    setTimeout(render, 30);
  });
  tbody.addEventListener('click', event => {
    if (!event.target.matches('.get-file')) return;
    const index = Number(event.target.closest('tr').dataset.index);
    if (retrievalMode === 'missing_dialog') return;
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-label', 'Download Files');
    dialog.innerHTML = '<div role="button" tabindex="0" class="fm-download-button">same.pdf</div>' +
      '<button class="close">Close</button>';
    document.body.append(dialog);
    dialog.querySelector('.close').onclick = () => dialog.remove();
    dialog.querySelector('.fm-download-button').onclick = () => {
      if (retrievalMode === 'download_timeout') return;
      const invalid = retrievalMode === 'all_fail' || (retrievalMode === 'partial' && index === 1);
      const payload = invalid ? '<html>server error</html>' : window.fixturePdfBase64 ?
        Uint8Array.from(atob(window.fixturePdfBase64), c => c.charCodeAt(0)) : `%PDF-1.7\nfixture ${index}\n%%EOF\n`;
      const url = URL.createObjectURL(new Blob([payload], {type: 'application/pdf'}));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'same.pdf';
      document.body.append(link);
      link.click();
      link.remove();
    };
  });
});
