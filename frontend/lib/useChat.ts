"use client";

import { useCallback, useRef, useState } from "react";
import { streamChat } from "@/api/chat";
import { conversationsApi } from "@/api/conversations";
import type { ChatMessage, GraphStep } from "@/types/chat";

let nextId = 0;
const uid = () => `msg-${Date.now()}-${nextId++}`;

export const useChat = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [steps, setSteps] = useState<GraphStep[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback((text: string, convId: string | null) => {
    const userMsg: ChatMessage = {
      id: uid(),
      role: "user",
      content: text,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setSteps([]);
    setIsLoading(true);
    setError(null);

    if (convId) {
      conversationsApi
        .addMessage(convId, { role: "user", content: text })
        .catch(() => {});
    }

    const controller = streamChat(
      { question: text },
      {
        onStep(event) {
          setSteps((prev) => {
            const completed = prev.map((s) => ({
              ...s,
              status: "completed" as const,
            }));
            return [
              ...completed,
              { node: event.node, label: event.label, status: "active" },
            ];
          });
        },

        onResult(event) {
          const answer = event.answer;
          const content =
            typeof answer.response === "string"
              ? answer.response
              : JSON.stringify(answer, null, 2);
          const sql =
            typeof answer.sql_code === "string" ? answer.sql_code : undefined;

          const assistantMsg: ChatMessage = {
            id: uid(),
            role: "assistant",
            content,
            sql,
            timestamp: Date.now(),
          };

          setMessages((prev) => [...prev, assistantMsg]);
          setSteps((prev) =>
            prev.map((s) => ({ ...s, status: "completed" as const })),
          );
          setIsLoading(false);
          controllerRef.current = null;

          if (convId) {
            conversationsApi
              .addMessage(convId, {
                role: "assistant",
                content,
                sqlCode: sql ?? null,
              })
              .catch(() => {});
          }
        },

        onError(event) {
          setError(event.message);
          setIsLoading(false);
          controllerRef.current = null;
        },
      },
    );

    controllerRef.current = controller;
  }, []);

  const stopGeneration = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setIsLoading(false);
  }, []);

  const clearMessages = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setMessages([]);
    setSteps([]);
    setIsLoading(false);
    setError(null);
  }, []);

  return {
    messages,
    setMessages,
    steps,
    isLoading,
    error,
    sendMessage,
    stopGeneration,
    clearMessages,
  };
};
