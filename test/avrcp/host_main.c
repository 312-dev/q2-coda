/* test/q2_avrcp.py's q2-avrcp: patch/q2_avrcp.c built for this machine (CODA_HOST), its passes run until killed. */
int qa_step(void);
int main(void) {
    for (;;)
        if (!qa_step()) return 1;
}
