const $ = (selector) => document.querySelector(selector);
const form = $('#entry-form');
const fields = ['title', 'category', 'content', 'source', 'valid_from', 'valid_until'];
const names = {teacher:'Преподаватель',schedule:'Расписание',announcement:'Объявление',other:'Другое'};
const endpoint = '/admin/api/documents';
let documents = [];
let selected = null;
let busy = false;

function notice(text, error = false) {
  $('#notice').textContent = text;
  $('#notice').toggleAttribute('data-error', error);
}

async function api(path, method = 'GET', data) {
  const response = await fetch(endpoint + path, {
    method, credentials:'same-origin', cache:'no-store',
    headers: {'Content-Type':'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (response.status === 204) return;
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Ошибка сервера (${response.status}).`);
  return result;
}

function render() {
  const query = $('#filter').value.trim().toLocaleLowerCase('ru');
  const list = $('#entries');
  list.replaceChildren();
  for (const doc of documents) {
    if (!`${doc.title} ${names[doc.category]} ${doc.content}`.toLocaleLowerCase('ru').includes(query)) continue;
    const item = document.createElement('li');
    const button = document.createElement('button');
    const meta = document.createElement('small');
    button.type = 'button';
    button.textContent = doc.title;
    button.setAttribute('aria-current', doc.id === selected ? 'true' : 'false');
    button.onclick = () => { if (!busy) editor(doc); };
    meta.textContent = names[doc.category] + (doc.valid_until ? ` · до ${doc.valid_until}` : '');
    button.append(meta);
    item.append(button);
    list.append(item);
  }
  if (!list.children.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = query ? 'Совпадений нет.' : 'База пока пуста.';
    list.append(empty);
  }
  $('#export').disabled = busy || !documents.length;
}

function editor(doc) {
  form.reset();
  selected = doc?.id ?? null;
  if (doc) for (const key of fields) form.elements[key].value = doc[key] ?? '';
  $('#editor-title').textContent = doc ? 'Редактирование записи' : 'Новая запись';
  $('#delete').hidden = !doc;
  render();
}

async function refresh() {
  documents = (await api('')).documents;
  render();
}

async function work(action, message) {
  if (busy) return;
  busy = true;
  const controls = [...form.elements, $('#new'), $('#import'), $('#export')];
  controls.forEach((control) => { control.disabled = true; });
  if (message) notice(message);
  try { await action(); }
  catch (error) { notice(error.message, true); }
  finally {
    busy = false;
    controls.forEach((control) => { control.disabled = false; });
    render();
  }
}

$('#filter').oninput = render;
$('#new').onclick = () => { editor(); form.elements.title.focus(); };
$('#text-file').onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 100_000) throw new Error('Текстовый файл должен содержать до 24 000 символов.');
    const text = await file.text();
    if (text.length > 24000) throw new Error('Текстовый файл должен содержать до 24 000 символов.');
    form.elements.content.value = text;
    if (!form.elements.title.value) form.elements.title.value = file.name.replace(/\.[^.]+$/, '').slice(0, 180);
    notice('Текст загружен в форму.');
  } catch (error) {
    notice(error.message, true);
  } finally {
    event.target.value = '';
  }
};

form.onsubmit = (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  work(async () => {
    const result = await api(selected ? '/' + selected : '', selected ? 'PUT' : 'POST', data);
    await refresh();
    editor(documents.find((doc) => doc.id === result.ids[0]));
    notice('Запись сохранена. Пятерка может использовать её в ответах.');
  }, 'Индексирование записи…');
};

$('#delete').onclick = () => {
  if (!selected || !confirm('Удалить выбранную запись из базы знаний?')) return;
  work(async () => {
    await api('/' + selected, 'DELETE');
    await refresh();
    editor();
    notice('Запись удалена.');
  }, 'Удаление записи…');
};

$('#import').onchange = (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  work(async () => {
    if (file.size >= 6 * 1024 ** 2) throw new Error('Размер файла должен быть меньше 6 МБ.');
    let records;
    try { records = JSON.parse((await file.text()).replace(/^\uFEFF/, '')); }
    catch { throw new Error('Файл должен содержать корректный JSON.'); }
    if (!Array.isArray(records)) throw new Error('Файл должен содержать массив записей.');
    const result = await api('/import', 'POST', records);
    await refresh();
    editor();
    notice(`Записей загружено: ${result.ids.length}.`);
  }, 'Импорт и индексирование записей…');
};

$('#export').onclick = () => {
  const records = documents.map((doc) => Object.fromEntries(fields.map((key) => [key, doc[key]])));
  const uri = URL.createObjectURL(new Blob([JSON.stringify(records, null, 2)], {type:'application/json'}));
  const link = Object.assign(document.createElement('a'), {href:uri, download:'iu5-knowledge.json'});
  link.click();
  setTimeout(() => URL.revokeObjectURL(uri), 500);
};

work(() => refresh(), 'Загрузка базы…').then(() => {
  if ($('#notice').textContent === 'Загрузка базы…') notice('');
});
