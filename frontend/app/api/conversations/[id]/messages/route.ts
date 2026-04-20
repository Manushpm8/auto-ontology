import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = await req.json();

  const message = await prisma.message.create({
    data: {
      conversationId: id,
      role: body.role,
      content: body.content ?? "",
      sqlCode: body.sqlCode ?? null,
    },
  });

  await prisma.conversation.update({
    where: { id },
    data: { updatedAt: new Date() },
  });

  return NextResponse.json(message, { status: 201 });
}
