import "dotenv/config";
import { defineConfig } from "prisma/config";

const user = process.env.POSTGRES_USER ?? "postgres";
const password = process.env.POSTGRES_PASSWORD ?? "";
const host = process.env.POSTGRES_HOST ?? "localhost";
const port = process.env.POSTGRES_PORT ?? "5432";
const database = process.env.POSTGRES_DATABASE ?? "gsf";

export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: {
    path: "prisma/migrations",
  },
  datasource: {
    url: `postgresql://${user}:${password}@${host}:${port}/${database}`,
  },
});
