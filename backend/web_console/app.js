import { generateTotp, importTotpKey, parseTotpSecret } from "./totp.js";

const byId = (id) => document.getElementById(id);
const state = { routes: [], filtered: [], selected: null, filter: "ALL" };

function setText(id, value) {
  byId(id).textContent = value;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function methodClass(method) {
  return method === "GET" ? "method-get" : method === "POST" ? "method-post" : "";
}

function canRunInConsole(route) {
  return route.method === "GET" &&
    (route.path.startsWith("/markets/") || route.path.startsWith("/horoscope/"));
}

function routeMatchesFilter(route) {
  const query = byId("route-search").value.trim().toLowerCase();
  const methodAllowed = state.filter === "ALL" ||
    route.method === state.filter ||
    (state.filter === "OTHER" && !["GET", "POST"].includes(route.method));
  return methodAllowed && (!query ||
    [route.method, route.path, route.summary, route.tag].some((part) => part.toLowerCase().includes(query)));
}

function renderRoutes() {
  const list = byId("routes-list");
  const routes = state.routes.filter(routeMatchesFilter);
  state.filtered = routes;
  setText("visible-count", routes.length + " / " + state.routes.length);
  list.replaceChildren();
  list.setAttribute("aria-busy", "false");

  if (!routes.length) {
    const empty = makeElement("div", "list-message");
    empty.append(makeElement("strong", "", state.routes.length ? "Ничего не найдено" : "Нет доступных маршрутов"));
    empty.append(makeElement("span", "", state.routes.length ? "Попробуй изменить запрос или фильтр." : "Проверь OpenAPI-схему сервера."));
    list.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const route of routes) {
    const row = makeElement("button", "route-row");
    row.type = "button";
    row.setAttribute("aria-label", route.method + " " + route.path + ", " + route.tag);
    row.classList.toggle("is-selected", state.selected?.id === route.id);
    row.setAttribute("aria-pressed", String(state.selected?.id === route.id));
    const main = makeElement("span", "route-row-main");
    const pathLine = makeElement("span", "route-pathline");
    pathLine.append(makeElement("span", "http-method " + methodClass(route.method), route.method));
    pathLine.append(makeElement("span", "route-path", route.path));
    main.append(pathLine, makeElement("span", "route-summary", route.summary || "Без описания"));
    row.append(main, makeElement("span", "route-tag", route.tag));
    row.addEventListener("click", () => selectRoute(route));
    fragment.append(row);
  }
  list.append(fragment);
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function formValues(route) {
  const result = {};
  const inputs = byId("parameter-fields").querySelectorAll("input[data-param]");
  for (const input of inputs) result[input.dataset.param] = input.value.trim();
  return result;
}

function buildUrl(route, requireAll = false) {
  const values = formValues(route);
  let routePath = route.path.replace(/\{([^}]+)\}/g, (match, name) => {
    if (!values[name]) {
      if (requireAll) throw new Error("Заполни обязательный параметр: " + name);
      return match;
    }
    return encodeURIComponent(values[name]);
  });
  if (!routePath.startsWith("/") || routePath.startsWith("//")) {
    throw new Error("Некорректный путь в OpenAPI-схеме.");
  }
  const url = new URL(routePath, window.location.origin);
  for (const parameter of route.parameters) {
    if (parameter.in === "query" && values[parameter.name]) {
      url.searchParams.set(parameter.name, values[parameter.name]);
    }
    if (requireAll && parameter.in === "query" && parameter.required && !values[parameter.name]) {
      throw new Error("Заполни обязательный параметр: " + parameter.name);
    }
  }
  return url;
}

function createCurl(route) {
  const url = buildUrl(route);
  let command = "curl -sS --request " + route.method + " " + shellQuote(url.toString()) +
    " \\\n  --header 'Accept: application/json'";
  if (route.requestBody) {
    command += " \\\n  --header 'Content-Type: application/json' \\\n  --data '{}'";
  }
  return command;
}

function updateCurl() {
  if (!state.selected) return;
  try {
    setText("curl-code", createCurl(state.selected));
  } catch (error) {
    setText("curl-code", "# " + error.message);
  }
}

function buildParameters(route) {
  const fields = byId("parameter-fields");
  fields.replaceChildren();
  for (const parameter of route.parameters) {
    if (!["path", "query"].includes(parameter.in)) continue;
    const wrapper = makeElement("div", "parameter-wrap");
    const label = makeElement("label", "", parameter.name);
    const input = makeElement("input");
    const inputId = "param-" + parameter.in + "-" + parameter.name.replace(/[^a-z0-9_-]/gi, "-");
    input.id = inputId;
    input.name = parameter.name;
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = parameter.schema?.type || "Значение";
    input.dataset.param = parameter.name;
    if (parameter.schema?.default !== undefined) input.value = String(parameter.schema.default);
    if (parameter.schema?.type === "integer" || parameter.schema?.type === "number") input.inputMode = "decimal";
    label.htmlFor = inputId;
    label.append(makeElement("span", "", parameter.required ? "обязательно" : "необязательно"));
    input.addEventListener("input", () => {
      input.removeAttribute("aria-invalid");
      updateCurl();
    });
    wrapper.append(label, input);
    fields.append(wrapper);
  }
}

function selectRoute(route) {
  state.selected = route;
  renderRoutes();
  setText("selected-method", route.method + " " + route.tag);
  setText("selected-description", route.description || route.summary || "Описание маршрута не задано.");
  buildParameters(route);
  byId("copy-curl").disabled = false;
  const safeToRun = canRunInConsole(route);
  byId("run-request").disabled = !safeToRun;
  byId("run-request").title = safeToRun ? "" : "Для этого маршрута используй Swagger или скопируй cURL.";
  byId("response-box").hidden = true;
  updateCurl();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

async function runGet(event) {
  event.preventDefault();
  const route = state.selected;
  if (!route || !canRunInConsole(route)) return;
  const form = byId("request-form");
  const button = byId("run-request");
  const responseBox = byId("response-box");
  let url;

  try {
    url = buildUrl(route, true);
  } catch (error) {
    setText("response-status", "Проверь параметры");
    setText("response-body", error.message);
    responseBox.hidden = false;
    const input = Array.from(form.querySelectorAll("input[data-param]")).find((field) => {
      const parameter = route.parameters.find((candidate) => candidate.name === field.dataset.param);
      return parameter?.required && !field.value.trim();
    });
    if (input) input.focus();
    return;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "Отправляем…";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    const body = await response.text();
    let pretty = body;
    if (response.headers.get("content-type")?.includes("json")) {
      try { pretty = JSON.stringify(JSON.parse(body), null, 2); } catch { /* Show raw body. */ }
    }
    setText("response-status", "HTTP " + response.status + " " + response.statusText);
    setText("response-body", pretty.length > 16000 ? pretty.slice(0, 16000) + "\n\nОтвет обрезан после 16 000 символов." : pretty || "(пустое тело ответа)");
  } catch (error) {
    setText("response-status", "Запрос не выполнен");
    setText("response-body", error.name === "AbortError" ? "Ответ не пришёл за 12 секунд." : error.message);
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = "Выполнить GET";
    responseBox.hidden = false;
  }
}

async function loadOpenApi() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/openapi.json", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const schema = await response.json();
    const routes = [];
    const methods = ["get", "post", "put", "patch", "delete", "options", "head"];
    for (const [path, operations] of Object.entries(schema.paths || {})) {
      for (const method of methods) {
        const operation = operations[method];
        if (!operation) continue;
        const parameters = [...(operations.parameters || []), ...(operation.parameters || [])];
        const normalized = {
          id: method + ":" + path,
          method: method.toUpperCase(),
          path,
          tag: operation.tags?.[0] || "other",
          summary: operation.summary || "",
          description: operation.description || "",
          parameters,
          requestBody: Boolean(operation.requestBody),
        };
        routes.push(normalized);
      }
    }
    state.routes = routes;
    setText("route-total", routes.length);
    setText("schema-version", schema.info?.version || "OpenAPI");
    const getCount = routes.filter((route) => route.method === "GET").length;
    const postCount = routes.filter((route) => route.method === "POST").length;
    setText("method-summary", getCount + " get, " + postCount + " post");
    byId("method-track").style.transform = "scaleX(" + (routes.length ? getCount / routes.length : 0) + ")";
    const label = byId("schema-state");
    label.textContent = "Схема загружена";
    label.classList.remove("is-error");
    label.classList.add("is-connected");
    renderRoutes();
    if (routes.length) selectRoute(routes.find(canRunInConsole) || routes[0]);
  } catch (error) {
    state.routes = [];
    setText("route-total", "…");
    setText("schema-state", "OpenAPI недоступен");
    byId("schema-state").classList.add("is-error");
    const list = byId("routes-list");
    list.setAttribute("aria-busy", "false");
    list.replaceChildren();
    const message = makeElement("div", "list-message");
    message.append(makeElement("strong", "", "Не удалось получить схему API"));
    message.append(makeElement("span", "", "Проверь /openapi.json или открой Swagger по ссылке."));
    const retry = makeElement("button", "key key-dark", "Повторить");
    retry.type = "button";
    retry.addEventListener("click", loadOpenApi);
    message.append(retry);
    list.append(message);
  } finally {
    clearTimeout(timeout);
  }
}

function setupDirectory() {
  byId("route-search").addEventListener("input", renderRoutes);
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((filter) => {
        const selected = filter === button;
        filter.classList.toggle("is-active", selected);
        filter.setAttribute("aria-pressed", String(selected));
      });
      renderRoutes();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey &&
      !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      byId("route-search").focus();
    }
  });
  byId("copy-curl").addEventListener("click", async () => {
    const button = byId("copy-curl");
    if (!state.selected) return;
    const copied = await copyText(byId("curl-code").textContent);
    button.textContent = copied ? "Команда скопирована" : "Не удалось скопировать";
    setTimeout(() => { button.textContent = "Скопировать cURL"; }, 1800);
  });
  byId("request-form").addEventListener("submit", runGet);
  byId("close-response").addEventListener("click", () => { byId("response-box").hidden = true; });
}

function setupTotp() {
  const input = byId("totp-secret");
  const copy = byId("copy-code");
  const message = byId("totp-message");
  const progress = byId("totp-progress");
  const toggle = byId("toggle-secret");
  let active = null;
  let nonce = 0;
  let lastCounter = -1;
  let displayedCode = "";

  function reset() {
    active = null;
    displayedCode = "";
    lastCounter = -1;
    setText("totp-code", "••• •••");
    byId("totp-code").classList.remove("is-ready");
    copy.disabled = true;
    setText("totp-period", "Код на 30 секунд");
    setText("totp-timer", "30 c");
    byId("timer-fill").style.transform = "scaleX(1)";
    progress.setAttribute("aria-valuenow", "30");
    progress.setAttribute("aria-valuemax", "30");
  }

  function setMessage(value, invalid = false) {
    message.textContent = value;
    message.classList.toggle("is-error", invalid);
    input.setAttribute("aria-invalid", String(invalid));
  }

  async function parseInput() {
    const turn = ++nonce;
    reset();
    if (!input.value.trim()) {
      setMessage("Ничего не сохраняется в истории или на сервере.");
      return;
    }
    try {
      const config = parseTotpSecret(input.value);
      const key = await importTotpKey(config);
      if (turn !== nonce) return;
      active = { config, key };
      setText("totp-period", "Код на " + config.period + " секунд");
      setMessage("Код вычисляется в этой вкладке. SHA-" + config.algorithm.split("-")[1] + ".");
      tick();
    } catch (error) {
      if (turn !== nonce) return;
      setMessage(error.message, true);
    }
  }

  async function tick() {
    if (!active) return;
    const snapshot = active;
    const seconds = Date.now() / 1000;
    const period = snapshot.config.period;
    const remaining = Math.max(0, period - (seconds % period));
    const secondsLeft = Math.ceil(remaining);
    setText("totp-timer", secondsLeft + " c");
    progress.setAttribute("aria-valuemax", String(period));
    progress.setAttribute("aria-valuenow", String(secondsLeft));
    byId("timer-fill").style.transform = "scaleX(" + remaining / period + ")";

    const counter = Math.floor(seconds / period);
    if (lastCounter === counter) return;
    lastCounter = counter;
    try {
      const code = await generateTotp(snapshot.config, snapshot.key, Math.floor(seconds));
      if (active !== snapshot || lastCounter !== counter) return;
      displayedCode = code;
      const midpoint = code.length / 2;
      setText("totp-code", code.slice(0, midpoint) + " " + code.slice(midpoint));
      byId("totp-code").classList.add("is-ready");
      copy.disabled = false;
    } catch {
      if (active !== snapshot) return;
      reset();
      setMessage("Не удалось вычислить код. Проверь секрет.", true);
    }
  }

  input.addEventListener("input", parseInput);
  input.addEventListener("blur", () => {
    if (input.value.trim() && !active) input.setAttribute("aria-invalid", "true");
  });
  toggle.addEventListener("click", () => {
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(visible));
    toggle.setAttribute("aria-label", visible ? "Скрыть секрет" : "Показать секрет");
  });
  copy.addEventListener("click", async () => {
    if (!displayedCode) return;
    const copied = await copyText(displayedCode);
    copy.textContent = copied ? "Код скопирован" : "Не удалось скопировать";
    setTimeout(() => { copy.textContent = "Скопировать код"; }, 1700);
  });
  byId("clear-secret").addEventListener("click", () => {
    ++nonce;
    input.value = "";
    input.type = "password";
    toggle.setAttribute("aria-pressed", "false");
    toggle.setAttribute("aria-label", "Показать секрет");
    reset();
    setMessage("Ничего не сохраняется в истории или на сервере.");
    input.focus();
  });
  document.addEventListener("visibilitychange", tick);
  setInterval(tick, 250);
  reset();
}

setupDirectory();
setupTotp();
loadOpenApi();
