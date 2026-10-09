// A seam so tests can observe full-page navigations (jsdom cannot perform them).
export function goTo(url: string) {
  window.location.assign(url);
}
