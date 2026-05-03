import { NextResponse } from 'next/server';
import { getPrisma } from '@/lib/prisma';

export async function GET() {
	const prisma = getPrisma();
	const prompts = await prisma.prompt.findMany();
	return NextResponse.json(prompts);
}

export async function POST(req: Request) {
	const prisma = getPrisma();
	const body = await req.json();
	const prompt = await prisma.prompt.create({
		data: {
			content: body.content ?? '',
		},
	});
	return NextResponse.json(prompt, { status: 201 });
}
