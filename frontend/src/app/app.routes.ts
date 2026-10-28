import { Routes } from '@angular/router';

import { authGuard, roleGuard } from './shared/auth.guard';

export const routes: Routes = [
  { path: 'login', title: 'Sign in', loadComponent: () => import('./pages/login').then((m) => m.LoginPage) },
  { path: '', pathMatch: 'full', redirectTo: 'monitoring' },
  { path: 'monitoring', title: 'Real-Time Monitoring', canActivate: [authGuard], loadComponent: () => import('./pages/monitoring').then((m) => m.MonitoringPage) },
  { path: 'quality', title: 'Data Quality', canActivate: [authGuard], loadComponent: () => import('./pages/quality').then((m) => m.QualityPage) },
  { path: 'alerts', title: 'Alerts', canActivate: [authGuard], loadComponent: () => import('./pages/alerts').then((m) => m.AlertsPage) },
  { path: 'impact', title: 'Risk & Business Impact', canActivate: [authGuard], loadComponent: () => import('./pages/impact').then((m) => m.ImpactPage) },
  {
    path: 'reports',
    title: 'Reports',
    canActivate: [authGuard, roleGuard('quality_supervisor')],
    loadComponent: () => import('./pages/reports').then((m) => m.ReportsPage),
  },
  {
    path: 'admin/users',
    title: 'User Management',
    canActivate: [authGuard, roleGuard('admin')],
    loadComponent: () => import('./pages/users').then((m) => m.UsersPage),
  },
  { path: '**', redirectTo: 'monitoring' },
];
