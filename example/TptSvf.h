#pragma once

#include "TptIntegrator.h"

/** A TPT state variable filter: one input, lowpass, bandpass and highpass outputs. */
class TptSvf
{
public:
    void setSampleRate(double sampleRate);
    void setCutoff(double hertz);

    /** 0 is no resonance, 1 is the edge of self-oscillation. */
    void setResonance(double amount);

    /** Filters one sample. Read the outputs afterwards. */
    void process(double input);

    double lowpass() const { return m_lowpass; }
    double bandpass() const { return m_bandpass; }
    double highpass() const { return m_highpass; }

    void reset();

private:
    void updateGain();

    TptIntegrator m_first;
    TptIntegrator m_second;

    double m_sampleRate = 44100.0;
    double m_cutoff = 1000.0;
    double m_gain = 0.0;
    double m_damping = 1.0;

    double m_lowpass = 0.0;
    double m_bandpass = 0.0;
    double m_highpass = 0.0;
};
