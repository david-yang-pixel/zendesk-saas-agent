/**
 * Return the path at which the web console is mounted by its host.
 *
 * Vite's BASE_URL includes a trailing slash (for example `/admin/`), while
 * URL concatenation in the API transport and router is easier with a stable
 * slash-free prefix.  The root mount intentionally becomes an empty prefix
 * so local development continues to use `/api` and `/harness`.
 */
export function adminBasePath(baseUrl: string = import.meta.env.BASE_URL): string {
  const normalized = baseUrl.trim()
  if (!normalized || normalized === '/') return ''
  return `/${normalized.replace(/^\/+|\/+$/g, '')}`
}

export function adminApiBasePath(baseUrl: string = import.meta.env.BASE_URL): string {
  return `${adminBasePath(baseUrl)}/api`
}
