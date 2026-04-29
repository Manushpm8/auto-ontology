import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	const prompt = await prisma.customPrompt.findUnique({ where: { id } });

	if (!prompt) {
		return NextResponse.json({ error: 'Custom prompt not found' }, { status: 404 });
	}

	return NextResponse.json(prompt);
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	const body = await req.json();

	const data: Record<string, string> = {};
	if (typeof body.content === 'string') data.content = body.content;

	const prompt = await prisma.customPrompt.update({
		where: { id },
		data,
	});
	return NextResponse.json(prompt);
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	await prisma.customPrompt.delete({ where: { id } });
	return new Response(null, { status: 204 });
}
