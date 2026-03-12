import React, { useState } from 'react';

const ChatStream: React.FC = () => {
    const [content, setContent] = useState<string>('');

    const handleStream = async () => {
        try {
            const response = await fetch('http://localhost:8080/proxy/chat/stream', {
                method: 'GET',
                headers: {
                    'Accept': 'text/event-stream'
                }
            });

            if (!response.body) {
                throw new Error('ReadableStream not supported');
            }

            /*
             * 关键：获取 reader 手动控制读取节奏。
             * 不要等待整个 response 完成。
             */
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                // 解码 chunk 并追加到 UI
                const chunk = decoder.decode(value, { stream: true });
                setContent((prev) => prev + chunk);
            }
        } catch (error) {
            console.error('Streaming error:', error);
        }
    };

    return (
        <div>
            <button onClick={handleStream}>Start Stream</button>
            <pre>{content}</pre>
        </div>
    );
};

export default ChatStream;