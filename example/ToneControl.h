#pragma once

#include "TptSvf.h"

/** A one-knob tone control: turn it down to darken the sound, up to leave it bright. */
class ToneControl
{
public:
    void prepare(double sampleRate);

    /** 0 is darkest, 1 is brightest. */
    void setTone(double amount);

    void process(double* samples, int numSamples);

private:
    TptSvf m_filter;
    double m_tone = 1.0;
};
