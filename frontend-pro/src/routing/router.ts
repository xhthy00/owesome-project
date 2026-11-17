import { history, useLocation, useParams } from '@umijs/max';
import { useMemo } from 'react';

type QueryValue = string | number | boolean | (string | number | boolean)[] | undefined | null;
type Destination = string | { pathname: string; query?: Record<string, QueryValue> };

export function routeHref(to: Destination): string {
  if (typeof to === 'string') return to;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(to.query ?? {})) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item != null) query.append(key, String(item));
    }
  }
  const search = query.toString();
  return search ? `${to.pathname}?${search}` : to.pathname;
}

export function useRouter() {
  const { pathname, search, hash } = useLocation();
  const params = useParams();
  const paramsKey = JSON.stringify(params);
  return useMemo(() => {
    const pathParams = JSON.parse(paramsKey) as Record<string, string | undefined>;
    const query: Record<string, string | string[] | undefined> = Object.create(null);
    new URLSearchParams(search).forEach((value, key) => {
      const previous = query[key];
      query[key] = previous === undefined ? value : [...(Array.isArray(previous) ? previous : [previous]), value];
    });
    Object.assign(query, pathParams);
    return {
      pathname, asPath: pathname + search + hash, query, isReady: true,
      push: async (to: Destination) => { history.push(routeHref(to)); return true; },
      replace: async (to: Destination) => { history.replace(routeHref(to)); return true; },
      reload: () => window.location.reload(),
    };
  }, [pathname, search, hash, paramsKey]);
}
