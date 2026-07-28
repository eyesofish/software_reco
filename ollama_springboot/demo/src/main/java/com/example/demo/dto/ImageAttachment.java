package com.example.demo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class ImageAttachment {
    private String name;
    @JsonProperty("media_type")
    private String mediaType;
    @JsonProperty("data_url")
    private String dataUrl;

    public ImageAttachment() {
    }

    public ImageAttachment(String name, String mediaType, String dataUrl) {
        this.name = name;
        this.mediaType = mediaType;
        this.dataUrl = dataUrl;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getMediaType() {
        return mediaType;
    }

    public void setMediaType(String mediaType) {
        this.mediaType = mediaType;
    }

    public String getDataUrl() {
        return dataUrl;
    }

    public void setDataUrl(String dataUrl) {
        this.dataUrl = dataUrl;
    }
}
