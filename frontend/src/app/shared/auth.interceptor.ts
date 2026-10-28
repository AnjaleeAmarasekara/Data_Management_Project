import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';

const API_BASE = 'http://localhost:8000';

/** Attaches the bearer token to API calls, and logs out on a 401 (expired or missing
 * session) -- the same signal the backend uses for "not authenticated". */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.token();

  const authedReq = token && req.url.startsWith(API_BASE) ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(authedReq).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401) {
        auth.logout();
      }
      return throwError(() => err);
    }),
  );
};
