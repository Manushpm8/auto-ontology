import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

export async function GET() {
	const prisma = getPrisma();
	const conversations = await prisma.conversation.findMany({
		orderBy: { createdAt: 'desc' },
	});
	return NextResponse.json(conversations);
}

export async function POST(req: Request) {
	const prisma = getPrisma();
	const body = await req.json();
	const conversation = await prisma.conversation.create({
		data: { title: body.title ?? '' },
	});
	return NextResponse.json(conversation, { status: 201 });
}
