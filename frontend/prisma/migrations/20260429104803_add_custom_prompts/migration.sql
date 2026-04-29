-- CreateTable
CREATE TABLE "custom_prompts" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "content" TEXT NOT NULL DEFAULT '',

    CONSTRAINT "custom_prompts_pkey" PRIMARY KEY ("id")
);
