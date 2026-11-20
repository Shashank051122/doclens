function parseMessage(rawMessage) {
  let eventName = "message";
  const dataLines = [];

  for (const line of rawMessage.split("\n")) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  try {
    return { eventName, payload: JSON.parse(dataLines.join("\n")) };
  } catch (error) {
    return null;
  }
}

export async function streamUpload(file, handlers, signal) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/upload/stream", {
    method: "POST",
    body: formData,
    signal,
  });

  if (!response.ok || !response.body) {
    let message = "The server could not start reading this file.";
    try {
      const body = await response.json();
      if (body && body.detail && typeof body.detail.message === "string") {
        message = body.detail.message;
      }
    } catch (error) {
      // The error body was not JSON, so we keep the default message.
    }
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawMessage = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      const message = parseMessage(rawMessage);
      if (message && typeof handlers[message.eventName] === "function") {
        handlers[message.eventName](message.payload);
      }

      boundary = buffer.indexOf("\n\n");
    }
  }
}