import { timingSafeEqual } from 'node:crypto';
import { waitUntil, getDeadline } from '@vercel/functions';
import { createRefreshHandler } from '../server/refresh-dispatcher.mjs';

// One source per invocation, including the work awaited after the HTTP response.
export const config = { maxDuration: 300 };
export default createRefreshHandler({ waitUntil, getDeadline, timingSafeEqual });
