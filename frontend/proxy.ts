import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Generic transform: for /api/* requests, moves the first `*_id` query
 * parameter into the URL path so the backend receives it as a path param.
 *
 * Example: GET /api/schemas?db_id=abc  →  GET /api/schemas/abc
 */
export function proxy(request: NextRequest) {
	const url = request.nextUrl.clone();

	for (const [key, value] of url.searchParams.entries()) {
		if (key.endsWith('_id')) {
			url.pathname = `${url.pathname}/${encodeURIComponent(value)}`;
			url.searchParams.delete(key);
			return NextResponse.rewrite(url);
		}
	}

	return NextResponse.next();
}

export const config = {
	matcher: '/api/:path*',
};
