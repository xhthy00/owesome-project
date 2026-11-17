import { Link } from '@umijs/max';
import type { ComponentProps } from 'react';
import { routeHref } from './router';

export default function RouteLink({ href, ...props }: Omit<ComponentProps<typeof Link>, 'to'> & { href: Parameters<typeof routeHref>[0] }) {
  return <Link to={routeHref(href)} {...props} />;
}
