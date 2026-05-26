const cache = new Map();

export function $(id) {
  let node = cache.get(id);
  if (!node || !node.isConnected) {
    node = document.getElementById(id);
    if (!node) throw new Error(`Missing dashboard element: ${id}`);
    cache.set(id, node);
  }
  return node;
}

export function text(value) {
  return document.createTextNode(value == null ? "" : String(value));
}

export function clear(node) {
  node.replaceChildren();
}

export function append(parent, ...children) {
  for (const child of children.flat()) {
    if (child == null) continue;
    parent.append(child instanceof Node ? child : text(child));
  }
  return parent;
}

export function el(tagName, attrs = {}, children = []) {
  const node = document.createElement(tagName);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "className") {
      node.className = value;
    } else if (key === "textContent") {
      node.textContent = value;
    } else if (key === "dataset") {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        node.dataset[dataKey] = dataValue;
      }
    } else if (key === "style") {
      Object.assign(node.style, value);
    } else if (key in node) {
      node[key] = value;
    } else {
      node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

export function tableCell(tagName, children = [], attrs = {}) {
  return el(tagName, attrs, Array.isArray(children) ? children : [children]);
}

export function safeClassToken(value, fallback = "unknown") {
  const token = String(value || fallback).toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  return token || fallback;
}

export function setActive(node, active) {
  node.classList.toggle("active", active);
  if (node.hasAttribute("aria-pressed") || node.tagName === "BUTTON") {
    node.setAttribute("aria-pressed", active ? "true" : "false");
  }
}
