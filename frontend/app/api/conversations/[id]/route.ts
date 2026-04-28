import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	const conversation = await prisma.conversation.findUnique({
		where: { id },
		include: { messages: { orderBy: { createdAt: 'asc' } } },
	});

	if (!conversation) {
		return NextResponse.json({ error: 'Conversation not found' }, { status: 404 });
	}

	return NextResponse.json(conversation);
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	const body = await req.json();
	const conversation = await prisma.conversation.update({
		where: { id },
		data: { title: body.title },
	});
	return NextResponse.json(conversation);
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
	const prisma = getPrisma();
	const { id } = await params;
	await prisma.conversation.delete({ where: { id } });
	return new Response(null, { status: 204 });
}
