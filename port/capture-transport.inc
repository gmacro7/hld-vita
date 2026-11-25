static bool writeCaptureDeflate(FILE* output, mz_stream* stream, int flush) {
    unsigned char chunk[8192];
    do {
        stream->next_out = chunk;
        stream->avail_out = sizeof(chunk);
        const int status = mz_deflate(stream, flush);
        if (status != MZ_OK && status != MZ_STREAM_END) return false;
        const size_t produced = sizeof(chunk) - stream->avail_out;
        if (fwrite(chunk, 1, produced, output) != produced) return false;
        if (status == MZ_STREAM_END) return true;
    } while (stream->avail_in > 0 || flush == MZ_FINISH);
    return true;
}

static bool writeCompressedCapture(FILE* output, const uint32_t* pixels, int width, int height, int stride) {
    static const unsigned char header[] = "HLDRGBAZ";
    mz_stream stream = {0};
    if (mz_deflateInit(&stream, MZ_BEST_SPEED) != MZ_OK) return false;

    bool success = fwrite(header, 1, sizeof(header) - 1, output) == sizeof(header) - 1;
    for (int y = 0; y < height && success; ++y) {
        stream.next_in = (const unsigned char*)(pixels + (size_t)y * stride);
        stream.avail_in = (unsigned int)width * sizeof(*pixels);
        success = writeCaptureDeflate(output, &stream, MZ_NO_FLUSH);
    }
    if (success) success = writeCaptureDeflate(output, &stream, MZ_FINISH);
    if (stream.total_out + sizeof(header) - 1 >= (size_t)width * height * sizeof(*pixels)) success = false;
    mz_deflateEnd(&stream);
    return success;
}
