import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService, Role } from './auth.service';

export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.isLoggedIn() || router.createUrlTree(['/login']);
};

/** Route data: `{ minRole: 'quality_supervisor' }`. Redirects to the home page rather
 * than login, since the user IS authenticated -- they just lack the required role. */
export function roleGuard(minRole: Role): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);
    if (!auth.isLoggedIn()) return router.createUrlTree(['/login']);
    return auth.hasRole(minRole) || router.createUrlTree(['/monitoring']);
  };
}
